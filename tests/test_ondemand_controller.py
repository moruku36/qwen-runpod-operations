"""CPU-only safety tests for the isolated lifecycle controller."""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import json
import math
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from qmc_runpod.ondemand import ArtifactReceipt, ControllerError, CorruptCheckpoint, JsonStateStore, LifecycleController, MemoryStateStore, MockArtifactStore, MockAuthority, MockProvider, MockTransport, Phase, ReconcileResult, TerminationApproval, UsageLimits, UsageScope

class FakeClock:

    def __init__(self, value: float=100.0) -> None:
        self.value = value

    def now(self) -> float:
        return self.value

    def set(self, value: float) -> None:
        self.value = value

def make_limits(session: str, *, seconds: float=1000, usd: object='1.00', expiry: float=5000, cleanup_seconds: float=60, cleanup_usd: object='0.0002') -> UsageLimits:
    scope = UsageScope(session, 'new-pod', frozenset({'create', 'bootstrap', 'load', 'chat', 'export', 'terminate'}))
    return UsageLimits.create(max_runtime_seconds=seconds, max_usd=usd, expires_at=expiry, scope=scope, cleanup_runtime_seconds=cleanup_seconds, cleanup_usd=cleanup_usd)
TEST_AUTHORITIES = {}

def make_controller(*, store=None, clock=None, provider=None, transport=None, artifacts=None, idle=300, authority=None):
    store = store if store is not None else MemoryStateStore()
    key = str(store.path.resolve()) if isinstance(store, JsonStateStore) else store
    authority = authority if authority is not None else TEST_AUTHORITIES.setdefault(key, MockAuthority())
    return LifecycleController(store=store, clock=clock or FakeClock(), authority=authority, provider=provider or MockProvider(), transport=transport or MockTransport(), artifacts=artifacts or MockArtifactStore(), idle_timeout_seconds=idle)

def ready(controller: LifecycleController, session: str='session-a') -> None:
    controller.start(session, make_limits(session))
    assert controller.run_one_step() == 'created'
    assert controller.run_one_step() == 'bootstrapped'
    assert controller.run_one_step() == 'ready'

def export_pending(controller: LifecycleController, clock: FakeClock, idle: float=300) -> None:
    clock.set(clock.value + idle)
    assert controller.run_one_step() == 'draining'
    assert controller.run_one_step() == 'exporting'
    assert controller.run_one_step() == 'approval_pending'

def exact_approval(controller: LifecycleController, approval_id: str='approve-1') -> TerminationApproval:
    state = controller.store.snapshot()['session']
    return TerminationApproval(approval_id, 'terminate', state['id'], state['pod_id'], state['receipt']['sha256'], 4900)

class ControllerTests(unittest.TestCase):

    def test_import_default_construction_models_and_status_are_offline(self):
        with patch.object(socket, 'socket', side_effect=AssertionError('socket used')), patch.object(subprocess, 'Popen', side_effect=AssertionError('subprocess used')), patch.dict('os.environ', {}, clear=True):
            controller = make_controller()
            before = controller.store.snapshot()
            for _ in range(5):
                self.assertEqual(controller.list_models()[0]['id'], 'qwen-27b')
                self.assertTrue(controller.status()['simulated'])
            after = controller.store.snapshot()
            self.assertEqual(before, after)
            self.assertEqual(controller.provider.calls, [])
            self.assertEqual(controller.transport.calls, [])

    def test_chat_is_queued_until_ready_and_catalog_never_starts_or_chats(self):
        controller = make_controller()
        controller.start('session-a', make_limits('session-a'))
        controller.queue_chat('r1', 'private prompt', session_id=controller.status()['session_id'])
        self.assertEqual(controller.run_one_step(), 'created')
        self.assertEqual(controller.run_one_step(), 'bootstrapped')
        self.assertEqual(controller.run_one_step(), 'ready')
        self.assertEqual(controller.transport.calls, [('load', controller.store.snapshot()['session']['load_operation'])])
        controller.list_models()
        controller.list_models()
        self.assertEqual(controller.run_one_step(), 'chat:simulated response')
        self.assertEqual([kind for kind, _ in controller.transport.calls].count('chat'), 1)
        audit = json.dumps(controller.store.snapshot()['audit'])
        self.assertNotIn('private prompt', audit)

    def test_duplicate_concurrent_start_and_step_share_one_create_intent(self):
        store, provider, clock = (MemoryStateStore(), MockProvider(), FakeClock())
        first = make_controller(store=store, provider=provider, clock=clock)
        second = make_controller(store=store, provider=provider, clock=clock)
        barrier = threading.Barrier(2)

        def begin(controller):
            barrier.wait()
            controller.start('session-a', make_limits('session-a'))
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(begin, (first, second)))
            list(pool.map(lambda c: c.run_one_step(), (first, second)))
        self.assertEqual(sum((name == 'create' for name, _ in provider.calls)), 1)
        self.assertEqual(first.status()['pod_id'], second.status()['pod_id'])

    def test_two_controllers_deduplicate_same_queued_request(self):
        store, provider, transport, clock = (MemoryStateStore(), MockProvider(), MockTransport(), FakeClock())
        first = make_controller(store=store, provider=provider, transport=transport, clock=clock)
        second = make_controller(store=store, provider=provider, transport=transport, clock=clock)
        ready(first)
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda c: c.queue_chat('same-request', 'same payload', session_id=c.status()['session_id']), (first, second)))
            results = list(pool.map(lambda c: c.run_one_step(), (first, second)))
        self.assertEqual([kind for kind, _ in transport.calls].count('chat'), 1)
        self.assertEqual(sum((value == 'chat:simulated response' for value in results)), 1)
        self.assertEqual(first.status()['queue_depth'], 0)

    def test_create_timeout_malformed_and_truncated_reconcile_never_recreate(self):
        for mode in ('unknown',):
            provider = MockProvider()
            provider.create_mode = mode
            controller = make_controller(provider=provider)
            controller.start('session-a', make_limits('session-a'))
            self.assertEqual(controller.run_one_step(), 'create_unresolved')
            self.assertEqual(controller.run_one_step(), 'reconciliation_required')
            self.assertEqual(controller.reconcile_start(session_id=controller.status()['session_id']), 'unknown')
            self.assertEqual(controller.reconcile_start(session_id=controller.status()['session_id']), 'unknown')
            self.assertEqual(sum((name == 'create' for name, _ in provider.calls)), 1)
        provider = MockProvider()
        provider.create_mode = 'malformed'
        controller = make_controller(provider=provider)
        controller.start('session-malformed', make_limits('session-malformed'))
        self.assertEqual(controller.run_one_step(), 'create_unresolved')
        self.assertEqual(controller.reconcile_start(session_id=controller.status()['session_id']), 'found')
        self.assertEqual(sum((name == 'create' for name, _ in provider.calls)), 1)
        provider = MockProvider()
        provider.create_mode = 'timeout_after_create'
        controller = make_controller(provider=provider)
        controller.start('session-b', make_limits('session-b'))
        self.assertEqual(controller.run_one_step(), 'create_unresolved')
        self.assertEqual(controller.reconcile_start(session_id=controller.status()['session_id']), 'found')
        self.assertEqual(controller.status()['phase'], Phase.BOOTSTRAPPING.value)
        provider = MockProvider()
        provider.create_mode = 'unknown'
        controller = make_controller(provider=provider)
        controller.start('session-truncated', make_limits('session-truncated'))
        controller.run_one_step()
        provider.reconcile_create = lambda *_: ReconcileResult('absent', (), False)
        self.assertEqual(controller.reconcile_start(session_id=controller.status()['session_id']), 'unknown')
        self.assertEqual(sum((name == 'create' for name, _ in provider.calls)), 1)

    def test_abort_does_not_clear_ambiguous_create_or_authorize_a_new_session(self):
        provider = MockProvider()
        provider.create_mode = 'unknown'
        controller = make_controller(provider=provider)
        controller.start('session-a', make_limits('session-a'))
        self.assertEqual(controller.run_one_step(), 'create_unresolved')
        self.assertEqual(controller.abort(session_id=controller.status()['session_id']), 'create_unresolved')
        with self.assertRaises(ControllerError):
            controller.start('session-b', make_limits('session-b'))
        self.assertEqual(controller.reconcile_start(session_id=controller.status()['session_id']), 'unknown')
        self.assertEqual(sum((name == 'create' for name, _ in provider.calls)), 1)

    def test_deadline_boundary_clock_rollback_and_restart_do_not_extend_authority(self):
        clock, store = (FakeClock(100), MemoryStateStore())
        controller = make_controller(store=store, clock=clock)
        controller.start('session-a', make_limits('session-a', seconds=10))
        self.assertEqual(controller.run_one_step(), 'created')
        self.assertEqual(controller.run_one_step(), 'bootstrapped')
        self.assertEqual(controller.run_one_step(), 'ready')
        clock.set(109)
        self.assertEqual(controller.run_one_step(), 'ready')
        clock.set(80)
        restarted = make_controller(store=store, clock=clock, provider=controller.provider, transport=controller.transport, artifacts=controller.artifacts)
        self.assertEqual(restarted.run_one_step(), 'ready')
        clock.set(110)
        self.assertEqual(restarted.run_one_step(), 'draining')
        self.assertEqual(restarted.status()['phase'], Phase.DRAINING.value)
        with self.assertRaises(ControllerError):
            restarted.queue_chat('late', 'no', session_id=restarted.status()['session_id'])

    def test_idle_deadline_is_persisted_across_controller_restart(self):
        clock, store = (FakeClock(), MemoryStateStore())
        controller = make_controller(store=store, clock=clock, idle=10)
        ready(controller)
        clock.set(105)
        controller.queue_chat('r1', 'activity', session_id=controller.status()['session_id'])
        self.assertEqual(controller.run_one_step(), 'chat:simulated response')
        restarted = make_controller(store=store, clock=clock, provider=controller.provider, transport=controller.transport, artifacts=controller.artifacts, idle=5000)
        clock.set(114)
        self.assertEqual(restarted.run_one_step(), 'ready')
        clock.set(115)
        self.assertEqual(restarted.run_one_step(), 'draining')

    def test_runtime_and_usd_limits_are_separate_and_invalid_numbers_rejected(self):
        for value in (True, float('nan'), float('inf'), -1, None):
            with self.assertRaises(ValueError):
                make_limits('bad', seconds=value)
        for value in (True, 'NaN', 'Infinity', -1, None):
            with self.assertRaises(ValueError):
                make_limits('bad', usd=value)
        clock = FakeClock()
        controller = make_controller(clock=clock)
        controller.start('session-a', make_limits('session-a', usd='0.0102'))
        for _ in range(3): controller.run_one_step()
        controller.queue_chat('one', 'first', session_id=controller.status()['session_id'])
        self.assertEqual(controller.run_one_step(), 'chat:simulated response')
        with self.assertRaises(ControllerError):
            controller.queue_chat('two', 'second', session_id=controller.status()['session_id'])
        self.assertEqual(controller.status()['phase'], Phase.READY.value)

    def test_idle_queue_race_is_serialized_and_abort_drop_retry_are_explicit(self):
        clock, store = (FakeClock(), MemoryStateStore())
        controller = make_controller(store=store, clock=clock, idle=10)
        ready(controller)
        clock.set(110)
        barrier = threading.Barrier(2)

        def queue():
            barrier.wait()
            try:
                controller.queue_chat('r1', 'queued', session_id=controller.status()['session_id'])
            except ControllerError:
                return 'rejected-after-drain'
            return 'queued'
        with ThreadPoolExecutor(max_workers=2) as pool:
            result = pool.submit(queue)
            barrier.wait()
            idle_result = controller.run_one_step()
            result.result()
        if idle_result == 'draining':
            self.assertEqual(controller.drop_chat('r1', session_id=controller.status()['session_id']), False)
        else:
            self.assertEqual(controller.run_one_step(), 'chat:simulated response')
        self.assertEqual(controller.abort(session_id=controller.status()['session_id']), 'draining')

    def test_chat_timeout_keeps_idempotent_retry_and_request_drop_is_specific(self):
        transport = MockTransport()
        controller = make_controller(transport=transport)
        ready(controller)
        controller.queue_chat('drop-me', 'one', session_id=controller.status()['session_id'])
        controller.queue_chat('retry-me', 'two', session_id=controller.status()['session_id'])
        with self.assertRaises(ControllerError):
            controller.queue_chat('retry-me', 'changed payload', session_id=controller.status()['session_id'])
        self.assertTrue(controller.drop_chat('drop-me', session_id=controller.status()['session_id']))
        self.assertFalse(controller.drop_chat('drop-me', session_id=controller.status()['session_id']))
        transport.fail_next_chat = True
        self.assertEqual(controller.run_one_step(), 'chat_retry_queued')
        self.assertEqual(controller.run_one_step(), 'chat:simulated response')
        self.assertEqual([request_id for action, request_id in transport.calls if action == 'chat'], ['retry-me'])

    def test_export_and_exact_action_scoped_approval_gate_termination(self):
        clock, provider = (FakeClock(), MockProvider())
        controller = make_controller(clock=clock, provider=provider)
        ready(controller)
        export_pending(controller, clock)
        state = controller.store.snapshot()['session']
        wrong = TerminationApproval('wrong', 'terminate', state['id'], 'other-pod', state['receipt']['sha256'], 4900)
        with self.assertRaises(ControllerError):
            controller.submit_termination_approval(wrong)
        self.assertEqual([name for name, _ in provider.calls].count('terminate'), 0)
        controller.submit_termination_approval(exact_approval(controller))
        self.assertEqual(controller.confirm_termination(session_id=controller.status()['session_id'], approval_id=controller.store.snapshot()['session']['approval']['id']), 'absent')
        self.assertEqual(controller.status()['phase'], Phase.ABSENT.value)
        self.assertEqual([name for name, _ in provider.calls].count('terminate'), 1)
        self.assertEqual(provider.termination_allowlists, [frozenset({state['pod_id']})])
        with self.assertRaises(ControllerError):
            controller.start('session-a', make_limits('session-a'))

    def test_old_session_approval_cannot_authorize_a_new_pod_and_expiry_survives_rollback(self):
        clock, provider = (FakeClock(), MockProvider())
        controller = make_controller(clock=clock, provider=provider)
        ready(controller, 'old-session')
        export_pending(controller, clock)
        old_approval = exact_approval(controller, 'one-use')
        controller.submit_termination_approval(old_approval)
        self.assertEqual(controller.confirm_termination(session_id=controller.status()['session_id'], approval_id=controller.store.snapshot()['session']['approval']['id']), 'absent')
        ready(controller, 'new-session')
        new_pod = controller.status()['pod_id']
        self.assertNotEqual(new_pod, old_approval.pod_id)
        export_pending(controller, clock)
        with self.assertRaises(ControllerError):
            controller.submit_termination_approval(exact_approval(controller, 'one-use'))
        with self.assertRaises(ControllerError):
            controller.submit_termination_approval(old_approval)
        self.assertFalse(any((kind == 'terminate' and op == controller.store.snapshot()['session']['terminate_operation'] for kind, op in provider.calls)))
        clock, provider = (FakeClock(), MockProvider())
        controller = make_controller(clock=clock, provider=provider)
        ready(controller)
        export_pending(controller, clock)
        state = controller.store.snapshot()['session']
        expiring = TerminationApproval('expiring', 'terminate', state['id'], state['pod_id'], state['receipt']['sha256'], clock.value + 50)
        controller.submit_termination_approval(expiring)
        clock.set(clock.value + 51)
        with self.assertRaises(ControllerError):
            controller.confirm_termination(session_id=controller.status()['session_id'], approval_id=controller.store.snapshot()['session']['approval']['id'])
        clock.set(clock.value - 51)
        with self.assertRaises(ControllerError):
            controller.confirm_termination(session_id=controller.status()['session_id'], approval_id=controller.store.snapshot()['session']['approval']['id'])
        self.assertFalse(any((kind == 'terminate' for kind, _ in provider.calls)))

    def test_unverified_export_and_wrong_or_reused_receipts_block_terminate(self):
        clock, provider, artifacts = (FakeClock(), MockProvider(), MockArtifactStore())
        artifacts.tamper = True
        controller = make_controller(clock=clock, provider=provider, artifacts=artifacts)
        ready(controller)
        clock.set(clock.value + 300)
        self.assertEqual(controller.run_one_step(), 'draining')
        self.assertEqual(controller.run_one_step(), 'exporting')
        self.assertEqual(controller.run_one_step(), 'export_unverified')
        self.assertEqual(controller.status()['phase'], Phase.FAILED.value)
        with self.assertRaises(ControllerError):
            controller.submit_termination_approval(TerminationApproval('blocked', 'terminate', 'session-a', 'fake-pod', '1' * 64, 4900))
        self.assertFalse(any((name == 'terminate' for name, _ in provider.calls)))

    def test_termination_not_confirmed_is_not_reported_as_success(self):
        clock, provider = (FakeClock(), MockProvider())
        provider.terminate_mode = 'stays_present'
        controller = make_controller(clock=clock, provider=provider)
        ready(controller)
        export_pending(controller, clock)
        controller.submit_termination_approval(exact_approval(controller))
        self.assertEqual(controller.confirm_termination(session_id=controller.status()['session_id'], approval_id=controller.store.snapshot()['session']['approval']['id']), 'unconfirmed')
        self.assertEqual(controller.status()['phase'], Phase.TERMINATION_UNCONFIRMED.value)
        self.assertEqual(controller.reconcile_termination(session_id=controller.status()['session_id']), 'unconfirmed')
        pod_id = controller.status()['pod_id']
        provider.pods.pop(pod_id)
        self.assertEqual(controller.reconcile_termination(session_id=controller.status()['session_id']), 'absent')
        self.assertEqual(controller.status()['phase'], Phase.ABSENT.value)

    def test_file_checkpoint_restart_and_corruption_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.json'
            clock, provider = (FakeClock(), MockProvider())
            first = make_controller(store=JsonStateStore(path), clock=clock, provider=provider)
            first.start('session-a', make_limits('session-a'))
            first.run_one_step()
            first.run_one_step()
            first.run_one_step()
            restarted = make_controller(store=JsonStateStore(path), clock=clock, provider=provider)
            self.assertEqual(restarted.status()['phase'], Phase.READY.value)
            restarted.queue_chat('file-request', 'persisted prompt', session_id=restarted.status()['session_id'])
            self.assertEqual(restarted.run_one_step(), 'chat:simulated response')
            path.write_text('{truncated', encoding='utf-8')
            with self.assertRaises(CorruptCheckpoint):
                restarted.status()
            self.assertEqual(sum((kind == 'create' for kind, _ in provider.calls)), 1)

    def test_usage_scope_expiry_and_target_are_mandatory(self):
        controller = make_controller(clock=FakeClock(100))
        incomplete = UsageLimits.create(max_runtime_seconds=30, max_usd='1', expires_at=5000, scope=UsageScope('x', 'new-pod', frozenset({'create'})), cleanup_runtime_seconds=60, cleanup_usd='0.0002')
        with self.assertRaises(ControllerError):
            controller.start('x', incomplete)
        with self.assertRaises(ControllerError):
            controller.start('x', make_limits('x', expiry=99))
        wrong = UsageScope('y', 'arbitrary-pod', frozenset({'create', 'bootstrap', 'load', 'chat', 'export', 'terminate'}))
        limits = UsageLimits.create(max_runtime_seconds=30, max_usd='1', expires_at=5000, scope=wrong, cleanup_runtime_seconds=60, cleanup_usd='0.0002')
        with self.assertRaises(ControllerError):
            controller.start('y', limits)
if __name__ == '__main__':
    unittest.main()
