"""Synthetic regressions for the independent Phase 1 security review findings."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from qmc_runpod.ondemand import (
    CHAT_COST_USD, ControllerError, CorruptCheckpoint, JsonStateStore,
    LifecycleController, MemoryStateStore, MockAuthority, MockProvider,
    MockTransport, Phase, TerminationApproval,
)
from test_ondemand_controller import FakeClock, exact_approval, export_pending, make_controller, make_limits, ready


def confirm(controller):
    session = controller.store.snapshot()["session"]
    return controller.confirm_termination(session_id=session["id"], approval_id=session["approval"]["id"])


class ReviewRegressions(unittest.TestCase):
    def _termination_boundary(self, boundary, stage):
        clock = FakeClock(100)
        controller = make_controller(clock=clock, idle=10)
        controller.start("s", make_limits("s", seconds=10, cleanup_seconds=2))
        for _ in range(3): controller.run_one_step()
        clock.set(110)
        self.assertEqual([controller.run_one_step() for _ in range(3)],
                         ["draining", "exporting", "approval_pending"])
        session = controller.store.snapshot()["session"]
        expiry = 110.25 if boundary == "approval" else 150
        approval = TerminationApproval("boundary", "terminate", "s", session["pod_id"],
                                       session["receipt"]["sha256"], expiry)
        controller.submit_termination_approval(approval)
        initial = 110 if boundary == "approval" else 110.5
        advanced = 110.5 if boundary == "approval" else 111.5
        clock.set(initial)
        original_verify, original_save = controller.artifacts.verify, controller._save

        def delayed_verify(*args):
            result = original_verify(*args)
            if stage == "verify": clock.set(advanced)
            return result

        def delayed_save(lock):
            original_save(lock)
            if stage == "intent_save" and lock.load()["phase"] == Phase.TERMINATION_UNCONFIRMED.value:
                clock.set(advanced)

        class DelayedAdapter:
            @property
            def terminate(self):
                clock.set(advanced)
                return provider.terminate

        provider = controller.provider
        if stage == "adapter_lookup": controller.provider = DelayedAdapter()
        with patch.object(controller.artifacts, "verify", side_effect=delayed_verify), \
             patch.object(controller, "_save", side_effect=delayed_save):
            with self.assertRaises(ControllerError): confirm(controller)
        self.assertFalse(any(k == "terminate" for k, _ in provider.calls))
        checkpoint = controller.store.snapshot()
        self.assertEqual(checkpoint["last_now"], advanced)
        if stage != "verify":
            self.assertEqual(checkpoint["used_approvals"], [approval.approval_id])
            self.assertEqual(confirm(controller), "unconfirmed")
            self.assertFalse(any(k == "terminate" for k, _ in provider.calls))
        else:
            self.assertEqual(checkpoint["used_approvals"], [])

    def test_termination_approval_expiry_is_rechecked_after_verify_and_before_dispatch(self):
        for stage in ("verify", "intent_save", "adapter_lookup"):
            with self.subTest(stage=stage): self._termination_boundary("approval", stage)

    def test_termination_cleanup_slot_is_rechecked_after_verify_and_before_dispatch(self):
        for stage in ("verify", "intent_save", "adapter_lookup"):
            with self.subTest(stage=stage): self._termination_boundary("cleanup", stage)

    def test_separate_json_stores_share_os_lock_without_reading_locked_byte(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            authority, provider, clock = MockAuthority(), MockProvider(), FakeClock()
            first = make_controller(store=JsonStateStore(path), authority=authority, provider=provider, clock=clock)
            second = make_controller(store=JsonStateStore(path), authority=authority, provider=provider, clock=clock)
            with ThreadPoolExecutor(max_workers=2) as pool:
                list(pool.map(lambda c: c.start("s", make_limits("s")), (first, second)))
                list(pool.map(lambda c: c.run_one_step(), (first, second)))
            self.assertEqual(sum(k == "create" for k, _ in provider.calls), 1)

    def test_runtime_boundary_reaches_verified_export_using_bounded_cleanup_reserve(self):
        clock = FakeClock()
        controller = make_controller(clock=clock, idle=10)
        controller.start("s", make_limits("s", seconds=10, cleanup_seconds=5))
        for _ in range(3): controller.run_one_step()
        clock.set(110)
        self.assertEqual(controller.run_one_step(), "draining")
        self.assertEqual(controller.run_one_step(), "exporting")
        self.assertEqual(controller.run_one_step(), "approval_pending")
        self.assertFalse(any(k == "terminate" for k, _ in controller.provider.calls))
        controller.submit_termination_approval(exact_approval(controller))
        self.assertEqual(confirm(controller), "absent")

    def test_usd_boundary_preserves_export_and_termination_reservations(self):
        clock = FakeClock()
        controller = make_controller(clock=clock, idle=10)
        controller.start("s", make_limits("s", usd="0.0102"))
        for _ in range(3): controller.run_one_step()
        controller.queue_chat("r", "fake", session_id="s")
        self.assertEqual(controller.run_one_step(), "chat:simulated response")
        with self.assertRaises(ControllerError): controller.queue_chat("over", "fake", session_id="s")
        export_pending(controller, clock, idle=10)
        self.assertEqual(controller.store.snapshot()["session"]["spent_usd"], "0.0101")
        controller.submit_termination_approval(exact_approval(controller))
        self.assertEqual(confirm(controller), "absent")

    def test_chat_incremental_cost_is_rejected_before_transport_effect(self):
        controller = make_controller()
        controller.start("s", make_limits("s", usd="0.001"))
        for _ in range(3): controller.run_one_step()
        with self.assertRaises(ControllerError): controller.queue_chat("r", "fake", session_id="s")
        self.assertFalse(any(k == "chat" for k, _ in controller.transport.calls))
        self.assertEqual(controller.store.snapshot()["session"]["spent_usd"], "0")

    def test_two_queued_chats_cannot_spend_a_single_chat_allowance_twice(self):
        controller = make_controller()
        controller.start("s", make_limits("s", usd="0.0102"))
        for _ in range(3): controller.run_one_step()
        controller.queue_chat("one", "fake", session_id="s")
        controller.queue_chat("two", "fake", session_id="s")
        self.assertEqual(controller.run_one_step(), "chat:simulated response")
        self.assertEqual(controller.run_one_step(), "draining")
        self.assertEqual(sum(k == "chat" for k, _ in controller.transport.calls), 1)
        self.assertEqual(controller.store.snapshot()["session"]["spent_usd"], "0.01")

    def test_expired_cleanup_cannot_export_or_terminate_automatically(self):
        clock = FakeClock()
        controller = make_controller(clock=clock)
        controller.start("s", make_limits("s", seconds=10, cleanup_seconds=2))
        for _ in range(3): controller.run_one_step()
        clock.set(112)
        self.assertEqual(controller.run_one_step(), "cleanup_expired")
        self.assertEqual(controller.status()["phase"], Phase.CLEANUP_EXPIRED.value)
        self.assertEqual(controller.artifacts.receipts, {})
        self.assertFalse(any(k == "terminate" for k, _ in controller.provider.calls))

    def test_cleanup_reserves_must_be_explicit_and_sufficient(self):
        for seconds, usd in ((1, "0.0002"), (60, "0.0001"), (60, True)):
            with self.assertRaises(ValueError): make_limits("s", cleanup_seconds=seconds, cleanup_usd=usd)
        # The final second is reserved for terminate; an export requiring one second cannot start later.
        clock = FakeClock()
        controller = make_controller(clock=clock)
        controller.start("s", make_limits("s", seconds=10, cleanup_seconds=2))
        for _ in range(3): controller.run_one_step()
        clock.set(110); controller.run_one_step(); controller.run_one_step()
        clock.set(111.1)
        self.assertEqual(controller.run_one_step(), "cleanup_expired")
        self.assertEqual(controller.artifacts.receipts, {})

    def test_lost_terminate_response_consumes_approval_once_and_reconciles_only(self):
        class LostTerminate(MockProvider):
            attempts = 0
            def terminate(self, *args):
                self.attempts += 1
                raise TimeoutError("simulated uncertain result")
        clock, provider = FakeClock(), LostTerminate()
        controller = make_controller(clock=clock, provider=provider, idle=10)
        ready(controller); export_pending(controller, clock, idle=10)
        approval = exact_approval(controller)
        controller.submit_termination_approval(approval)
        self.assertEqual(confirm(controller), "unconfirmed")
        self.assertEqual(confirm(controller), "unconfirmed")
        restored = LifecycleController(store=controller.store, clock=clock, provider=provider,
                    artifacts=controller.artifacts, authority=controller.authority)
        self.assertEqual(confirm(restored), "unconfirmed")
        self.assertEqual(restored.reconcile_termination(session_id=approval.session_id), "unconfirmed")
        self.assertEqual(provider.attempts, 1)
        self.assertEqual(controller.store.snapshot()["used_approvals"], [approval.approval_id])

    def test_crash_after_effect_and_before_result_checkpoint_does_not_reissue_terminate(self):
        clock, provider = FakeClock(), MockProvider()
        controller = make_controller(clock=clock, provider=provider, idle=10)
        ready(controller); export_pending(controller, clock, idle=10)
        approval = exact_approval(controller)
        controller.submit_termination_approval(approval)
        # Simulate lost absence read, followed by a failed result checkpoint write.
        with patch.object(provider, "is_absent", return_value=False):
            original_save = controller._save
            def fail_after_effect(lock):
                if any(k == "terminate" for k, _ in provider.calls): raise OSError("simulated result checkpoint failure")
                original_save(lock)
            with patch.object(controller, "_save", side_effect=fail_after_effect):
                with self.assertRaises(OSError): confirm(controller)
        restored = LifecycleController(store=controller.store, clock=clock, provider=provider,
                    artifacts=controller.artifacts, authority=controller.authority)
        self.assertEqual(confirm(restored), "unconfirmed")
        self.assertEqual(restored.reconcile_termination(session_id=approval.session_id), "absent")
        self.assertEqual(sum(k == "terminate" for k, _ in provider.calls), 1)

    def test_failed_intent_checkpoint_never_sends_terminate(self):
        clock = FakeClock(); controller = make_controller(clock=clock, idle=10)
        ready(controller); export_pending(controller, clock, idle=10)
        controller.submit_termination_approval(exact_approval(controller))
        original_save = controller._save
        def fail_consumption_save(lock):
            if lock.load()["phase"] == Phase.TERMINATION_UNCONFIRMED.value:
                raise OSError("simulated intent checkpoint failure")
            original_save(lock)
        with patch.object(controller, "_save", side_effect=fail_consumption_save):
            with self.assertRaises(OSError): confirm(controller)
        self.assertFalse(any(k == "terminate" for k, _ in controller.provider.calls))
        with self.assertRaises(CorruptCheckpoint): controller.run_one_step()

    def test_bool_or_malformed_deletion_approval_is_rejected_before_any_effect(self):
        clock = FakeClock(); controller = make_controller(clock=clock, idle=10)
        ready(controller); export_pending(controller, clock, idle=10)
        good = exact_approval(controller)
        for field, value in (("approval_id", True), ("expires_at", True), ("expires_at", float("nan")),
                             ("pod_id", 1), ("receipt_sha256", "bad"), ("action", "stop")):
            data = {"approval_id": good.approval_id, "action": good.action, "session_id": good.session_id,
                    "pod_id": good.pod_id, "receipt_sha256": good.receipt_sha256, "expires_at": good.expires_at}
            data[field] = value
            with self.assertRaises(ValueError): TerminationApproval(**data)
        self.assertFalse(any(k == "terminate" for k, _ in controller.provider.calls))

    def test_concurrent_start_rechecks_clock_and_expiry_inside_mutation_lock(self):
        store, clock, provider, authority = MemoryStateStore(), FakeClock(), MockProvider(), MockAuthority()
        first = make_controller(store=store, clock=clock, provider=provider, authority=authority, idle=10)
        second = make_controller(store=store, clock=clock, provider=provider, authority=authority, idle=10)
        started = threading.Event(); errors = []
        def stale_start():
            started.set()
            try: second.start("B", make_limits("B", expiry=150))
            except ControllerError as exc: errors.append(str(exc))
        # A retained outer RLock forces B to wait until A's committed high-water is 210.
        with store.locked():
            thread = threading.Thread(target=stale_start); thread.start(); self.assertTrue(started.wait(2))
            clock.set(200); ready(first, "A"); export_pending(first, clock, idle=10)
            first.submit_termination_approval(exact_approval(first)); self.assertEqual(confirm(first), "absent")
            clock.set(100)
        thread.join(2); self.assertFalse(thread.is_alive())
        self.assertTrue(errors)
        self.assertEqual(store.snapshot()["last_now"], 210)
        self.assertIsNone(store.snapshot()["session"])
        self.assertEqual(sum(k == "create" for k, _ in provider.calls), 1)

    def test_stale_drop_abort_queue_and_confirmation_cannot_target_a_new_session(self):
        clock = FakeClock(); controller = make_controller(clock=clock, idle=10)
        ready(controller, "old"); export_pending(controller, clock, idle=10)
        old = exact_approval(controller)
        controller.submit_termination_approval(old); confirm(controller)
        ready(controller, "new"); controller.queue_chat("shared", "new payload", session_id="new")
        with self.assertRaises(ControllerError): controller.drop_chat("shared", session_id="old")
        with self.assertRaises(ControllerError): controller.abort(session_id="old")
        with self.assertRaises(ControllerError): controller.queue_chat("stale", "fake", session_id="old")
        with self.assertRaises(ControllerError): controller.confirm_termination(session_id="old", approval_id=old.approval_id)
        self.assertEqual(controller.status()["queue_depth"], 1)
        with self.assertRaises(TypeError): controller.drop_chat("shared")

    def test_coherent_checkpoint_extension_and_usd_reset_cannot_restore_authority(self):
        controller = make_controller(); ready(controller)
        controller.queue_chat("r", "fake", session_id="session-a"); controller.run_one_step()
        snapshot = controller.store.snapshot()
        snapshot["session"]["spent_usd"] = "0"
        snapshot["session"]["limits"]["expires_at"] = 99999
        snapshot["session"]["limits"]["max_runtime_seconds"] = 99999
        snapshot["session"]["deadline"] = 99999 - 60
        snapshot["session"]["cleanup_deadline"] = 99999
        restored_store = MemoryStateStore(json.dumps(snapshot))
        for authority in (MockAuthority(), controller.authority):
            restored = LifecycleController(store=restored_store, authority=authority,
                                          provider=controller.provider, clock=controller.clock)
            with self.assertRaises(CorruptCheckpoint): restored.run_one_step()
        self.assertEqual(sum(k == "create" for k, _ in controller.provider.calls), 1)

    def test_even_unchanged_active_checkpoint_requires_original_injected_authority(self):
        controller = make_controller(); ready(controller)
        restored = LifecycleController(store=MemoryStateStore(json.dumps(controller.store.snapshot())), clock=controller.clock)
        with self.assertRaises(CorruptCheckpoint): restored.run_one_step()
        restored = LifecycleController(store=controller.store, clock=controller.clock, authority=controller.authority,
                                       provider=controller.provider, transport=controller.transport, artifacts=controller.artifacts)
        self.assertEqual(restored.run_one_step(), "ready")

    def test_active_request_or_foreign_receipt_in_cleanup_is_invalid(self):
        clock = FakeClock(); controller = make_controller(clock=clock, idle=10)
        ready(controller); export_pending(controller, clock, idle=10)
        for change in ("active", "receipt"):
            with controller.store.locked() as lock:
                session = lock.load()["session"]
                if change == "active": session["active"] = {"id": "pending", "prompt": "fake"}
                else: session["receipt"]["session_id"] = "foreign"
                with self.assertRaises(CorruptCheckpoint): lock.save()
        self.assertFalse(any(k == "terminate" for k, _ in controller.provider.calls))

    def test_late_chat_result_cannot_reset_authority_or_return_success(self):
        clock = FakeClock(); transport = MockTransport()
        controller = make_controller(clock=clock, transport=transport)
        controller.start("s", make_limits("s", seconds=10))
        for _ in range(3): controller.run_one_step()
        controller.queue_chat("r", "fake", session_id="s")
        original_chat = transport.chat
        def delayed(*args):
            result = original_chat(*args); clock.set(110); return result
        with patch.object(transport, "chat", side_effect=delayed):
            self.assertEqual(controller.run_one_step(), "chat_deadline_exceeded")
        self.assertEqual(controller.store.snapshot()["session"]["deadline"], 110)
        self.assertEqual(controller.status()["phase"], Phase.DRAINING.value)

    def test_fresh_import_and_actual_default_are_offline_under_environment_read_guards(self):
        class NoEnv(dict):
            def __getitem__(self, key): raise AssertionError("environment read")
            def get(self, *args): raise AssertionError("environment read")
            def __iter__(self): raise AssertionError("environment read")
        name = "ondemand_fresh_offline_probe"
        spec = importlib.util.spec_from_file_location(name, ROOT / "qmc_runpod/ondemand.py")
        module = importlib.util.module_from_spec(spec); sys.modules[name] = module
        try:
            with patch.object(socket, "socket", side_effect=AssertionError("socket")), \
                 patch.object(subprocess, "Popen", side_effect=AssertionError("subprocess")), \
                 patch.object(os, "environ", NoEnv()), patch.object(os, "getenv", side_effect=AssertionError("environment")):
                spec.loader.exec_module(module)
                controller = module.LifecycleController(); before = controller.store.snapshot()
                controller.status(); controller.list_models()
                self.assertEqual(before, controller.store.snapshot())
                self.assertEqual(controller.provider.calls, [])
                self.assertEqual(controller.transport.calls, [])
        finally: sys.modules.pop(name, None)


if __name__ == "__main__":
    unittest.main()
