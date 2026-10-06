"""No real user/account; trusted verifier is a public fixture callback."""
from dataclasses import replace
from pathlib import Path
import sys
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from qmc_runpod.owui_intent_policy import IntentPolicyError, OWUIIntentPolicy, ServerPurpose
from qmc_runpod.private_gateway import PrivateGateway
import test_private_gateway as fixtures
from test_private_gateway import body, request


class OWUIIntentPolicyTests(unittest.TestCase):
    def setUp(self):
        self.guards = fixtures.PrivateGatewayTests(); self.guards.setUp(); self.addCleanup(self.guards.doCleanups)
        self.gateway = self.guards.gateway()
        self.verified = {"subject": "fixture-user"}
        self.server_request = object()
        self.policy = OWUIIntentPolicy(self.gateway, verify_subject=lambda request: self.verified["subject"],
                                      allowed_subjects=frozenset({"fixture-user", "other-user"}))

    def context(self):
        return self.policy.begin(self.server_request, server_route="/api/chat/completions",
                                 server_purpose=ServerPurpose.MANUAL_CHAT)

    def test_default_verifier_subject_and_wrong_version_deny(self):
        policy = OWUIIntentPolicy(PrivateGateway())
        with self.assertRaises(IntentPolicyError):
            policy.begin(object(), server_route="/api/chat/completions", server_purpose=ServerPurpose.MANUAL_CHAT)
        with self.assertRaises(IntentPolicyError): OWUIIntentPolicy(self.gateway, version="0.11.5")
        self.verified["subject"] = "unauthorized"
        with self.assertRaises(IntentPolicyError): self.context()
        self.assertEqual(self.gateway.binding.upstream.calls, 0)

    def test_background_purposes_never_mint_even_when_original_route_reused(self):
        for purpose in ServerPurpose:
            if purpose is ServerPurpose.MANUAL_CHAT: continue
            with self.subTest(purpose=purpose), self.assertRaises(IntentPolicyError):
                self.policy.begin(self.server_request, server_route="/api/chat/completions", server_purpose=purpose)
        for purpose in ("manual_chat", True, None, {"task": "manual_chat"}):
            with self.subTest(purpose=purpose), self.assertRaises(IntentPolicyError):
                self.policy.begin(self.server_request, server_route="/api/chat/completions", server_purpose=purpose)
        self.assertEqual(self.gateway._intents, {})

    def test_wrong_route_and_client_metadata_cannot_authorize(self):
        for route in ("/api/tasks/title/completions", "/api/v1/models", "/api/chat/completions?manual=1"):
            with self.assertRaises(IntentPolicyError):
                self.policy.begin(self.server_request, server_route=route, server_purpose=ServerPurpose.MANUAL_CHAT)
        context = self.context()
        with self.assertRaises(Exception):
            self.policy.issue_ready(context, dict(body(), metadata={"user_id": "fixture-user", "task": "manual_chat"}),
                                    "r", server_purpose=ServerPurpose.MANUAL_CHAT)
        self.assertEqual(self.gateway.binding.upstream.calls, 0)

    def test_copied_or_foreign_context_is_not_authority(self):
        context = self.context()
        other = OWUIIntentPolicy(self.gateway, verify_subject=lambda request: "fixture-user",
                                 allowed_subjects=frozenset({"fixture-user"}))
        foreign = other.begin(object(), server_route="/api/v1/chat/completions", server_purpose=ServerPurpose.MANUAL_CHAT)
        for invalid in (replace(context), foreign, {"subject": "fixture-user"}):
            with self.assertRaises(IntentPolicyError):
                self.policy.issue_ready(invalid, body(), "r", server_purpose=ServerPurpose.MANUAL_CHAT)

    def test_changed_user_expired_context_and_background_consumption_deny(self):
        context = self.context()
        self.verified["subject"] = "other-user"
        with self.assertRaisesRegex(IntentPolicyError, "changed_verified_subject"):
            self.policy.issue_ready(context, body(), "r", server_purpose=ServerPurpose.MANUAL_CHAT)
        self.verified["subject"] = "fixture-user"
        context = self.context(); expired = replace(context, expires_at=time.monotonic() - 1)
        self.policy._contexts[context.sequence] = expired
        with self.assertRaises(IntentPolicyError):
            self.policy.issue_ready(expired, body(), "r", server_purpose=ServerPurpose.MANUAL_CHAT)
        context = self.context()
        with self.assertRaises(IntentPolicyError):
            self.policy.issue_ready(context, body(), "r", server_purpose=ServerPurpose.TITLE)
        self.assertEqual(self.gateway._intents, {})

    def test_verified_manual_context_drives_standard_gateway_request_once(self):
        context = self.context()
        forward = self.policy.issue_ready(context, body(), "manual-request", server_purpose=ServerPurpose.MANUAL_CHAT)
        self.assertNotIn(forward.intent_token, repr(forward))
        with self.assertRaises(IntentPolicyError):
            self.policy.issue_ready(context, body(), "manual-request", server_purpose=ServerPurpose.MANUAL_CHAT)
        with self.gateway.serve() as address:
            status, _, _ = request(address, "POST", "/v1/chat/completions", body(), rid=forward.request_id, token=forward.intent_token)
            self.assertEqual(status, 200)
        self.assertEqual(self.gateway.binding.upstream.calls, 1)
        self.assertEqual(self.gateway.controller.http_release_count, 1)

    def test_closed_policy_does_not_reauthenticate_or_mint(self):
        context = self.context(); self.policy.close()
        with self.assertRaises(IntentPolicyError):
            self.policy.issue_ready(context, body(), "r", server_purpose=ServerPurpose.MANUAL_CHAT)
        with self.assertRaises(IntentPolicyError): self.context()
        self.assertEqual(self.gateway._intents, {})

    def test_verifier_wait_cannot_renew_expired_manual_context(self):
        context = self.context()
        short = replace(context, expires_at=time.monotonic() + .01)
        self.policy._contexts[context.sequence] = short
        def late(request):
            threading.Event().wait(.03)
            return "fixture-user"
        self.policy._verify = late
        with self.assertRaisesRegex(IntentPolicyError, "manual_context_required"):
            self.policy.issue_ready(short, body(), "r", server_purpose=ServerPurpose.MANUAL_CHAT)
        self.assertEqual(self.gateway._intents, {})
        self.assertEqual(self.gateway.binding.upstream.calls, 0)


if __name__ == "__main__": unittest.main()
