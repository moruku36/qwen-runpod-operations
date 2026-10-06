"""Offline integrated C1 flow; public fixtures and explicit temp files only."""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from qmc_runpod.c1_authority import AuthorityJournal, DurableMockAuthority
from qmc_runpod.c1_ports import BootstrapPlans, PortError, ProviderPlans, Rate
from qmc_runpod.c1_runtime import OnDemandRuntime
from qmc_runpod.ondemand import CorruptCheckpoint, MockAuthority, MemoryStateStore, TerminationApproval, UsageLimits, UsageScope, REQUIRED_ACTIONS
from qmc_runpod.owui_intent_policy import ServerPurpose
from qmc_runpod.owui_v0114_offline import OfflineOWUICallSites
from qmc_runpod.private_chat import TunnelEndpoint
from qmc_runpod.upstream_sse import LoopbackUpstream
import test_mock_gateway as guards
from test_private_gateway import body, request
from test_ondemand_controller import FakeClock
from test_owui_upstream import server

KEY = b"public-mock-signing-fixture-key-32bytes"


def grant(sid="s", *, usd="10", work=2000, expiry=5000):
    return UsageLimits.create(max_runtime_seconds=work, max_usd=usd, expires_at=expiry,
        scope=UsageScope(sid, "new-pod", REQUIRED_ACTIONS), cleanup_runtime_seconds=120, cleanup_usd="0.10")


class C1FlowTests(unittest.TestCase):
    def setUp(self):
        self.guards = guards.GatewayTests(); self.guards.setUp(); self.addCleanup(self.guards.doCleanups)
        self.directory = tempfile.TemporaryDirectory(); self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)

    def journal(self): return AuthorityJournal(self.path / "authority.sqlite", self.path / "protected-head.json", key=KEY)

    def runtime(self, **kwargs):
        kwargs.setdefault("clock", FakeClock()); kwargs.setdefault("idle_seconds", 10)
        kwargs.setdefault("verify_subject", lambda request: "fixture-user")
        kwargs.setdefault("allowed_subjects", frozenset({"fixture-user"}))
        return OnDemandRuntime(**kwargs)

    def send(self, runtime, rid="r", sid="s", payload=None):
        return OfflineOWUICallSites(runtime).chat_entry(object(), body() if payload is None else payload,
            server_route="/api/chat/completions", purpose=ServerPurpose.MANUAL_CHAT, request_id=rid, session_id=sid)

    def startup(self, runtime):
        self.assertEqual([runtime.tick() for _ in range(3)], ["created", "bootstrapped", "ready"])

    def pending_cleanup(self, runtime):
        runtime.controller.clock.set(runtime.controller.store.snapshot()["session"]["idle_deadline"])
        self.assertEqual([runtime.tick() for _ in range(3)], ["draining", "exporting", "approval_pending"])

    def approval(self, runtime):
        s = runtime.controller.store.snapshot()["session"]
        return TerminationApproval("approve-"+s["id"], "terminate", s["id"], s["pod_id"], s["receipt"]["sha256"], s["cleanup_deadline"])

    def test_default_construction_imports_and_catalog_are_inert(self):
        with patch.object(socket, "socket", side_effect=AssertionError("socket")), patch("subprocess.Popen", side_effect=AssertionError("process")):
            runtime = OnDemandRuntime(); journal = self.journal()
            self.assertFalse(journal.path.exists()); self.assertEqual(runtime.controller.provider.calls, [])
            before = runtime.status()
            for _ in range(5): runtime.gateway.binding.catalog(); runtime.status()
            self.assertEqual(before, runtime.status())
            with self.assertRaises(Exception): self.send(runtime)
        self.assertEqual(runtime.controller.provider.calls, [])

    def test_background_wrong_model_and_unapproved_manual_never_create(self):
        runtime = self.runtime(); sites = OfflineOWUICallSites(runtime)
        for purpose in ServerPurpose:
            if purpose is ServerPurpose.MANUAL_CHAT: continue
            with self.assertRaises(Exception): sites.background(object(), body(), purpose=purpose, request_id="r", session_id="s")
        with self.assertRaises(PortError): self.send(runtime)
        with self.assertRaises(PortError): self.send(runtime, payload=dict(body(), model="gpt-unknown"))
        self.assertEqual(runtime.controller.provider.calls, [])

    def test_single_create_under_concurrent_manual_queue_and_readonly_status(self):
        runtime = self.runtime(); runtime.approve_session(grant())
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda i: self.send(runtime, rid="r"+str(i)), range(4)))
        before = runtime.controller.store.snapshot()
        for _ in range(10): runtime.status()
        self.assertEqual(before, runtime.controller.store.snapshot())
        self.startup(runtime)
        self.assertEqual(sum(action == "create" for action, _ in runtime.controller.provider.calls), 1)
        self.assertEqual(runtime.status()["queued"], 4)

    def test_queue_limit_payload_freeze_and_drop_before_create(self):
        runtime = self.runtime(); runtime.approve_session(grant())
        source = body(); self.send(runtime, payload=source); source["messages"][0]["content"] = "changed"
        self.assertNotIn(b"changed", runtime._queued["r"].payload)
        self.assertNotIn("public CPU fixture", repr(runtime._queued["r"]))
        self.assertTrue(runtime.drop("r")); self.assertEqual(runtime.tick(), "idle")
        self.assertEqual(runtime.controller.provider.calls, [])
        runtime.approve_session(grant("s2"))
        for i in range(8): self.send(runtime, rid="x"+str(i), sid="s2")
        with self.assertRaises(PortError): self.send(runtime, rid="overflow", sid="s2")

    def test_cold_load347_seconds_starts_idle_once_without_work_extension(self):
        runtime = self.runtime(); runtime.approve_session(grant()); self.send(runtime)
        runtime.tick(); runtime.tick(); original = runtime.controller.store.snapshot()["session"]["deadline"]
        runtime.controller.clock.set(447.956); self.assertEqual(runtime.tick(), "ready")
        s = runtime.controller.store.snapshot()["session"]
        self.assertEqual(s["idle_deadline"], 457.956); self.assertEqual(s["deadline"], original)
        runtime.tick(); self.assertEqual(runtime.controller.store.snapshot()["session"]["idle_deadline"], s["idle_deadline"])
        self.assertEqual(runtime.ready_request("r")[0], body())

    def test_startup_past_work_deadline_never_dispatches_or_renews(self):
        runtime = self.runtime(); runtime.approve_session(grant(work=30)); self.send(runtime)
        runtime.tick(); runtime.controller.clock.set(131)
        self.assertEqual(runtime.tick(), "budget_draining")
        with self.assertRaises(PortError): runtime.ready_request("r")
        self.assertEqual(runtime.gateway.binding.upstream.calls, 0)

    def test_unknown_create_reconciles_without_second_post(self):
        runtime = self.runtime(); runtime.approve_session(grant()); self.send(runtime)
        runtime.controller.provider.create_mode = "timeout_after_create"
        self.assertEqual(runtime.tick(), "create_unresolved")
        self.assertEqual(runtime.tick(), "reconciliation_required")
        self.assertEqual(runtime.reconcile_create("s"), "found")
        self.assertEqual(runtime.tick(), "bootstrapped"); self.assertEqual(runtime.tick(), "ready")
        self.assertEqual(len([r for r in runtime.plans.requests if r.method == "POST"]), 1)

    def test_http_json_sse_idle_export_exact_approval_then_next_recreate(self):
        runtime = self.runtime(); runtime.approve_session(grant()); self.send(runtime, rid="json")
        self.send(runtime, rid="sse", payload=body(True)); self.startup(runtime)
        idle = runtime.controller.store.snapshot()["session"]["idle_deadline"]
        with runtime.gateway.serve() as address:
            for rid in ("json", "sse"):
                payload, headers = runtime.ready_request(rid)
                status, _, wire = request(address, "POST", "/v1/chat/completions", payload, rid=rid, token=headers["X-Intent-Token"])
                self.assertEqual(status, 200)
                if rid == "sse": self.assertEqual(wire.count(b"data: [DONE]"), 1)
            self.assertEqual(runtime.controller.store.snapshot()["session"]["idle_deadline"], idle)
            self.pending_cleanup(runtime); approval = self.approval(runtime)
            self.assertTrue(runtime.exports.verify(approval.receipt_sha256))
            self.assertEqual(runtime.tick(), "approval_required")
            self.assertFalse(any(a == "terminate" for a, _ in runtime.controller.provider.calls))
            runtime.approve_cleanup(approval); self.assertEqual(runtime.confirm_cleanup("s", approval.approval_id), "absent")
            self.assertTrue(runtime.exports.verify(approval.receipt_sha256))
            runtime.approve_session(grant("s2")); self.send(runtime, rid="new", sid="s2"); self.startup(runtime)
            self.assertEqual(sum(a == "create" for a, _ in runtime.controller.provider.calls), 2)

    def test_whole_private_loopback_http_flow_with_owui_bookkeeping(self):
        with server() as (upstream, seen):
            runtime = self.runtime(upstream=LoopbackUpstream(TunnelEndpoint(upstream[1]), allow_loopback_io=True))
            runtime.approve_session(grant()); payload = dict(body(True), metadata={"task": "untrusted"}, chat_id="fixture")
            self.send(runtime, payload=payload); self.startup(runtime)
            clean, headers = runtime.ready_request("r")
            with runtime.gateway.serve() as address:
                status, _, wire = request(address, "POST", "/v1/chat/completions", clean, token=headers["X-Intent-Token"], rid="r")
                self.assertEqual(status, 200); self.assertIn(b"data: [DONE]", wire)
            self.assertEqual(len(seen), 1); self.assertNotIn("metadata", seen[0][2]); self.assertNotIn("chat_id", seen[0][2])

    def test_elapsed_rate_budget_drains_during_startup(self):
        runtime = self.runtime(rate=Rate(Decimal("20"), Decimal("0")))
        # Cleanup reserve cannot cover120s at this rate, so no allocation occurs.
        runtime.approve_session(grant(usd="1"))
        with self.assertRaises(PortError): self.send(runtime)
        self.assertEqual(runtime.controller.provider.calls, [])
        runtime = self.runtime(rate=Rate(Decimal("1.8"), Decimal(".05")))
        runtime.approve_session(grant(usd="1")); self.send(runtime); runtime.tick()
        runtime.controller.clock.set(1900)
        self.assertEqual(runtime.tick(), "budget_draining")
        self.assertEqual(runtime.controller.status()["phase"], "draining")

    def test_missing_tampered_export_and_wrong_approval_refuse_termination(self):
        runtime = self.runtime(); runtime.approve_session(grant()); self.send(runtime); self.startup(runtime)
        runtime.drop("r"); runtime.tick(); runtime.tick(); approval = self.approval(runtime)
        runtime.exports._objects[approval.receipt_sha256] = b"tampered"
        with self.assertRaises(PortError): runtime.approve_cleanup(approval)
        self.assertFalse(any(a == "terminate" for a, _ in runtime.controller.provider.calls))

    def test_provider_plans_default_deny_owner_allowlist_and_no_retry(self):
        plans = ProviderPlans(); operation = hashlib.sha256(b"create").hexdigest()
        dto = plans.create("s", operation)
        with self.assertRaises(PortError): plans.dispatch(dto)
        with self.assertRaises(PortError): plans.create("s", operation)
        with self.assertRaises(PortError): plans.acknowledge("s", operation, {"id": "synthetic-held-pod"}, account=plans.spec.account)
        plans.acknowledge("s", operation, {"id": "owned-fixture"}, account=plans.spec.account)
        with self.assertRaises(PortError): plans.terminate("other", "owned-fixture", operation, frozenset({"owned-fixture"}))
        with self.assertRaises(PortError): plans.terminate("s", "owned-fixture", operation, frozenset({"owned-fixture", "other"}))
        self.assertNotIn("Authorization", dto.body.decode()); self.assertEqual(json.loads(dto.body)["ports"], ["22/tcp"])

    def test_readiness_requires_pins_authenticated_endpoint_and_owner(self):
        port = BootstrapPlans()
        values = dict(fingerprint="1"*64, model_sha256=port.pins.model_sha256, llama_commit=port.pins.llama_commit)
        with self.assertRaises(PortError): port.attest_mock("s", "pod", **values)
        with self.assertRaises(PortError): port.attest_mock("s", "pod", **dict(values, model_sha256="0"*64), authenticated=True, process_owned=True)
        self.assertFalse(port.ready("s", "pod"))

    def test_durable_restart_preserves_grant_cost_idle_and_drops_pending_body(self):
        journal = self.journal()
        with journal.open():
            runtime = self.runtime(journal=journal); runtime.approve_session(grant()); self.send(runtime); self.startup(runtime)
            before = runtime.controller.store.snapshot(); runtime.close()
        with self.journal().open() as resumed:
            runtime = self.runtime(journal=resumed)
            after = runtime.controller.store.snapshot()
            self.assertEqual(before, after)
            self.assertEqual(runtime.status()["requests"], {"r": "interrupted_not_replayed"})
            with self.assertRaises(PortError): runtime.ready_request("r")
            self.assertEqual(runtime.gateway.binding.upstream.calls, 0)
            self.assertEqual(runtime.tick(), "ready")
            self.assertEqual(runtime.controller.store.snapshot()["session"]["idle_deadline"], before["session"]["idle_deadline"])
            runtime.close()
        self.assertNotIn(b"public CPU fixture", (self.path / "authority.sqlite").read_bytes())

    def test_untrusted_checkpoint_cannot_restore_authority(self):
        runtime = self.runtime(); runtime.approve_session(grant()); self.send(runtime)
        raw = runtime.controller.store._raw
        with self.assertRaises(CorruptCheckpoint): MockAuthority().check(MemoryStateStore(raw).snapshot())

    def test_single_owner_lock_and_ledger_only_rollback_refuse(self):
        journal = self.journal()
        with journal.open():
            runtime = self.runtime(journal=journal); runtime.approve_session(grant()); self.send(runtime)
            old = journal.path.read_bytes(); runtime.tick(); runtime.close()
            with self.assertRaises(CorruptCheckpoint):
                with self.journal().open(): pass
        journal.path.write_bytes(old)
        with self.assertRaises(CorruptCheckpoint):
            with self.journal().open(): pass

    def test_head_write_before_failed_append_is_fail_closed(self):
        journal = self.journal()
        with journal.open():
            runtime = self.runtime(journal=journal)
            original = journal._advance_head
            def fail_after_head(expected, value): original(expected, value); raise CorruptCheckpoint("synthetic_crash")
            with patch.object(journal, "_advance_head", side_effect=fail_after_head):
                runtime.approve_session(grant())
                with self.assertRaises(CorruptCheckpoint): self.send(runtime)
            self.assertEqual(runtime.controller.provider.calls, [])
        with self.assertRaises(CorruptCheckpoint):
            with self.journal().open(): pass

    def test_clock_rollback_and_changed_rate_configuration_fail_recovery(self):
        with self.journal().open() as journal:
            runtime = self.runtime(journal=journal); runtime.approve_session(grant()); self.send(runtime); self.startup(runtime)
            runtime.controller.clock.set(105); runtime.tick(); runtime.close()
        with self.journal().open() as journal:
            with self.assertRaises(CorruptCheckpoint): self.runtime(journal=journal, clock=FakeClock(99))
            with self.assertRaises(CorruptCheckpoint): self.runtime(journal=journal, clock=FakeClock(105), rate=Rate(Decimal("1")))

    def test_ledger_tamper_and_missing_head_fail_before_effect(self):
        with self.journal().open() as journal:
            runtime = self.runtime(journal=journal); runtime.close()
        self.path.joinpath("protected-head.json").unlink()
        with self.assertRaises(CorruptCheckpoint):
            with self.journal().open(): pass

    def test_explicit_queue_retry_keeps_original_deadline_and_no_redispatch(self):
        runtime = self.runtime(); runtime.approve_session(grant()); self.send(runtime)
        before = runtime.controller.store.snapshot()["session"]["deadline"]
        context = runtime.policy.begin(object(), server_route="/api/chat/completions", server_purpose=ServerPurpose.MANUAL_CHAT)
        self.assertEqual(runtime.retry_queued(context, "r")["deadline"], before)
        self.startup(runtime); runtime.ready_request("r")
        context = runtime.policy.begin(object(), server_route="/api/chat/completions", server_purpose=ServerPurpose.MANUAL_CHAT)
        with self.assertRaises(PortError): runtime.retry_queued(context, "r")
        self.assertEqual(runtime.controller.store.snapshot()["session"]["deadline"], before)

    def test_dispatch_checks_elapsed_rate_without_poll_and_close_blocks_effects(self):
        runtime = self.runtime(); runtime.approve_session(grant(usd="1")); self.send(runtime); self.startup(runtime)
        runtime.controller.clock.set(1900)
        with self.assertRaises(PortError): runtime.ready_request("r")
        self.assertEqual(runtime.gateway.binding.upstream.calls, 0)
        runtime.close(); calls = list(runtime.controller.provider.calls)
        with self.assertRaises(PortError): runtime.tick()
        self.assertEqual(calls, runtime.controller.provider.calls)

    def test_restart_unknown_create_uses_same_operation_and_no_auto_allocation(self):
        with self.journal().open() as journal:
            runtime = self.runtime(journal=journal); runtime.approve_session(grant()); self.send(runtime)
            runtime.controller.provider.create_mode = "timeout_after_create"
            self.assertEqual(runtime.tick(), "create_unresolved")
            operation = runtime.controller.store.snapshot()["session"]["create_operation"]
            runtime.close()
        with self.journal().open() as journal:
            runtime = self.runtime(journal=journal)
            self.assertEqual(runtime.tick(), "reconciliation_required")
            self.assertEqual(runtime.reconcile_create("s"), "found")
            self.assertEqual(runtime.controller.store.snapshot()["session"]["create_operation"], operation)
            self.assertFalse(any(a == "create" for a, _ in runtime.controller.provider.calls))

    def test_export_and_consumed_approval_survive_restart_without_delete_replay(self):
        with self.journal().open() as journal:
            runtime = self.runtime(journal=journal); runtime.approve_session(grant()); self.send(runtime); self.startup(runtime)
            runtime.drop("r"); runtime.tick(); runtime.tick(); approval = self.approval(runtime)
            runtime.approve_cleanup(approval); runtime.controller.provider.terminate_mode = "unknown"
            self.assertEqual(runtime.confirm_cleanup("s", approval.approval_id), "unconfirmed")
            runtime.close()
        with self.journal().open() as journal:
            runtime = self.runtime(journal=journal)
            self.assertTrue(runtime.exports.verify(approval.receipt_sha256))
            self.assertEqual(runtime.confirm_cleanup("s", approval.approval_id), "unconfirmed")
            self.assertFalse(any(a == "terminate" for a, _ in runtime.controller.provider.calls))

    def test_expired_cleanup_approval_cannot_even_plan_delete(self):
        runtime = self.runtime(); runtime.approve_session(grant()); self.send(runtime); self.startup(runtime)
        runtime.drop("r"); runtime.tick(); runtime.tick(); approval = self.approval(runtime)
        runtime.approve_cleanup(approval); runtime.controller.clock.set(approval.expires_at)
        with self.assertRaises(PortError): runtime.confirm_cleanup("s", approval.approval_id)
        self.assertFalse(any(r.method == "DELETE" for r in runtime.plans.requests))
        self.assertFalse(any(a == "terminate" for a, _ in runtime.controller.provider.calls))

    def test_modified_sql_payload_is_rejected_by_trusted_head_chain(self):
        with self.journal().open() as journal:
            runtime = self.runtime(journal=journal); runtime.approve_session(grant()); self.send(runtime); runtime.close()
        connection = sqlite3.connect(self.path / "authority.sqlite")
        try:
            connection.execute("UPDATE journal SET payload=? WHERE sequence=1", (b'{"forged":true}',)); connection.commit()
        finally: connection.close()
        with self.assertRaises(CorruptCheckpoint):
            with self.journal().open(): pass

    def test_stream_and_json_write_price_fences_catch_elapsed_cap_without_tick(self):
        runtime = self.runtime(); runtime.approve_session(grant(usd="1")); self.send(runtime, payload=body(True)); self.startup(runtime)
        payload, headers = runtime.ready_request("r"); permit = runtime.gateway._take({"x-intent-token": headers["X-Intent-Token"]})
        with runtime.gateway.binding.stream(payload, "r", permit=permit, deadline=time.monotonic()+1, cancel=threading.Event()) as events:
            first = next(events); self.assertIn(b'"role":"assistant"', first)
            runtime.controller.clock.set(1900)
            with self.assertRaises(Exception): next(events)
        self.assertEqual(runtime.controller.http_release_count, 1)
        with self.assertRaises(Exception):
            runtime.gateway._json_response_fence(time.monotonic()+1, runtime.gateway.binding._binding)

    def test_shutdown_durable_active_ticket_drains_and_never_replays_on_restore(self):
        with self.journal().open() as journal:
            runtime = self.runtime(journal=journal); runtime.approve_session(grant()); self.send(runtime, payload=body(True)); self.startup(runtime)
            payload, headers = runtime.ready_request("r"); permit = runtime.gateway._take({"x-intent-token": headers["X-Intent-Token"]})
            with runtime.gateway.binding.stream(payload, "r", permit=permit, deadline=time.monotonic()+1, cancel=threading.Event()) as events:
                next(events)
                # Save an actual active-ticket crash image, not normal close.
                crash = runtime.controller.store.snapshot()
                self.assertEqual(crash["session"]["active"]["id"], "r")
                saved = journal.latest()
            journal.append(saved)
        with self.journal().open() as journal:
            runtime = self.runtime(journal=journal)
            self.assertEqual(runtime.controller.status()["phase"], "draining")
            self.assertEqual(runtime.status()["requests"]["r"], "interrupted_not_replayed")
            self.assertEqual(runtime.gateway.binding.upstream.calls, 0)
            runtime.close()

    def test_restart_before_create_cannot_allocate_without_restored_manual_payload(self):
        with self.journal().open() as journal:
            runtime = self.runtime(journal=journal); runtime.approve_session(grant()); self.send(runtime); runtime.close()
        with self.journal().open() as journal:
            runtime = self.runtime(journal=journal)
            self.assertEqual(runtime.tick(), "idle")
            self.assertEqual(runtime.controller.provider.calls, [])
            self.assertEqual(runtime.status()["requests"]["r"], "interrupted_not_replayed")

    def test_drop_waiting_request_preserves_separate_dispatched_chat(self):
        runtime = self.runtime(); runtime.approve_session(grant()); self.send(runtime, rid="first"); self.send(runtime, rid="drop")
        self.startup(runtime); payload, headers = runtime.ready_request("first")
        self.assertTrue(runtime.drop("drop")); self.assertEqual(runtime.controller.status()["phase"], "ready")
        with runtime.gateway.serve() as address:
            self.assertEqual(request(address, "POST", "/v1/chat/completions", payload, rid="first", token=headers["X-Intent-Token"])[0], 200)

    def test_stop_none_or_wrong_session_is_not_an_active_session_shortcut(self):
        runtime = self.runtime(); runtime.approve_session(grant()); self.send(runtime); self.startup(runtime)
        before = runtime.controller.store.snapshot()
        for session_id in (None, "other", True):
            with self.assertRaises(Exception): runtime.stop(session_id)
        self.assertEqual(runtime.controller.store.snapshot(), before)

    def test_close_rejects_worker_that_checked_open_before_waiting_for_mutex(self):
        runtime = self.runtime(); runtime.approve_session(grant()); self.send(runtime)
        checked = threading.Event(); original = runtime._open_required
        def observed_open():
            original(); checked.set()
        with ThreadPoolExecutor(max_workers=1) as pool:
            with runtime._mutex, patch.object(runtime, "_open_required", observed_open):
                future = pool.submit(runtime.tick)
                self.assertTrue(checked.wait(1))
                runtime.close()
                before = runtime.controller.store.snapshot()
                self.assertEqual(runtime.controller.provider.calls, [])
            with self.assertRaisesRegex(PortError, "runtime_closed"): future.result(1)
        self.assertEqual(runtime.controller.store.snapshot(), before)
        self.assertEqual(runtime.controller.provider.calls, [])

    def test_actual_create_seal_crash_recovers_ownership_and_exact_cleanup(self):
        class Crash(BaseException): pass
        clock = FakeClock(100)
        with self.journal().open() as journal:
            runtime = self.runtime(journal=journal, clock=clock); runtime.approve_session(grant()); self.send(runtime)
            original = runtime.controller._save
            def save_then_crash(lock):
                original(lock)
                if lock.load()["phase"] == "bootstrapping": raise Crash()
            with patch.object(runtime.controller, "_save", save_then_crash):
                with self.assertRaises(Crash): runtime.tick()
            saved = journal.latest()
            pod = saved["state"]["session"]["pod_id"]
            self.assertEqual(saved["aux"]["provider"]["pods"][pod], "s")
            self.assertEqual(saved["aux"]["owned"][pod][0], "s")
        with self.journal().open() as journal:
            runtime = self.runtime(journal=journal, clock=clock)
            self.assertEqual(runtime.controller.status()["phase"], "draining")
            self.assertEqual([runtime.tick(), runtime.tick()], ["exporting", "approval_pending"])
            approval = self.approval(runtime); runtime.approve_cleanup(approval)
            self.assertEqual(runtime.confirm_cleanup("s", approval.approval_id), "absent")
            self.assertFalse(any(a == "create" for a, _ in runtime.controller.provider.calls)); runtime.close()

    def test_first_ready_seal_crash_has_atomic_idle_and_readiness(self):
        class Crash(BaseException): pass
        clock = FakeClock(100)
        with self.journal().open() as journal:
            runtime = self.runtime(journal=journal, clock=clock); runtime.approve_session(grant()); self.send(runtime)
            runtime.tick(); runtime.tick(); deadline = runtime.controller.store.snapshot()["session"]["deadline"]
            clock.set(447.956); original = runtime.controller._save
            def save_then_crash(lock):
                original(lock)
                if lock.load()["phase"] == "ready": raise Crash()
            with patch.object(runtime.controller, "_save", save_then_crash):
                with self.assertRaises(Crash): runtime.tick()
            saved = journal.latest()
            self.assertEqual(saved["aux"]["ready_once"], ["s"])
            self.assertAlmostEqual(saved["state"]["session"]["idle_deadline"], 457.956)
        with self.journal().open() as journal:
            runtime = self.runtime(journal=journal, clock=clock)
            self.assertEqual(runtime.tick(), "ready")
            session = runtime.controller.store.snapshot()["session"]
            self.assertEqual(session["deadline"], deadline)
            self.assertAlmostEqual(session["idle_deadline"], 457.956)
            self.assertTrue(runtime.bootstrap.ready("s", session["pod_id"])); runtime.close()

    def test_export_seal_crash_recovers_external_readback_without_reexport(self):
        class Crash(BaseException): pass
        with self.journal().open() as journal:
            runtime = self.runtime(journal=journal); runtime.approve_session(grant()); self.send(runtime); self.startup(runtime)
            runtime.stop("s"); runtime.tick(); original = runtime.controller._save
            def save_then_crash(lock):
                original(lock)
                if lock.load()["phase"] == "termination_approval_pending": raise Crash()
            with patch.object(runtime.controller, "_save", save_then_crash):
                with self.assertRaises(Crash): runtime.tick()
            approval = self.approval(runtime)
        with self.journal().open() as journal:
            runtime = self.runtime(journal=journal)
            self.assertTrue(runtime.exports.verify(approval.receipt_sha256))
            runtime.approve_cleanup(approval)
            self.assertEqual(runtime.confirm_cleanup("s", approval.approval_id), "absent"); runtime.close()

    def test_close_rejects_all_mutators_already_waiting_for_mutex(self):
        # Check every public mutator, including approval, dispatch and cleanup.
        for operation in ("approve", "accept", "stop", "drop", "dispatch", "retry", "reconcile", "cleanup_approve", "cleanup_confirm"):
            with self.subTest(operation=operation):
                runtime = self.runtime(); runtime.approve_session(grant()); self.send(runtime)
                context = runtime.policy.begin(object(), server_route="/api/chat/completions", server_purpose=ServerPurpose.MANUAL_CHAT)
                calls = {
                    "approve": lambda: runtime.approve_session(grant("next")),
                    "accept": lambda: runtime.accept(context, body(), "next", session_id="s"),
                    "stop": lambda: runtime.stop("s"), "drop": lambda: runtime.drop("r"),
                    "dispatch": lambda: runtime.ready_request("r"),
                    "retry": lambda: runtime.retry_queued(context, "r"),
                    "reconcile": lambda: runtime.reconcile_create("s"),
                    "cleanup_approve": lambda: runtime.approve_cleanup(None),
                    "cleanup_confirm": lambda: runtime.confirm_cleanup("s", "approval"),
                }
                checked = threading.Event(); original = runtime._open_required
                def observed_open():
                    original(); checked.set()
                with ThreadPoolExecutor(max_workers=1) as pool:
                    with runtime._mutex, patch.object(runtime, "_open_required", observed_open):
                        future = pool.submit(calls[operation]); self.assertTrue(checked.wait(1))
                        runtime.close(); before = runtime.controller.store.snapshot()
                        grants, queue = dict(runtime._approved), dict(runtime._queued)
                    with self.assertRaisesRegex(PortError, "runtime_closed"): future.result(1)
                self.assertEqual(runtime.controller.store.snapshot(), before)
                self.assertEqual(runtime._approved, grants); self.assertEqual(runtime._queued, queue)
                self.assertEqual(runtime.controller.provider.calls, [])


if __name__ == "__main__": unittest.main()
