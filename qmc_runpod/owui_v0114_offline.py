"""Pure projection of reviewed server call sites; never imports/patches OWUI.

Only trusted server call sites may set purpose and verified request context. The
existing request object can be reused for background work; purpose remains an
independent explicit enum. Authenticated user ID is a C2 configuration fact.
"""
from .c1_ports import PortError
from .owui_intent_policy import ServerPurpose


class OfflineOWUICallSites:
    def __init__(self, runtime):
        self.runtime = runtime

    def chat_entry(self, verified_request, final_form, *, server_route, purpose, request_id, session_id):
        if type(final_form) is not dict or final_form.get("model") != "qwen-27b":
            raise PortError("selected_private_model_required")
        # OWUI bookkeeping is not authority and is not forwarded to inference.
        known = {"model", "messages", "stream", "max_tokens", "metadata", "chat_id", "id", "parent_id"}
        if set(final_form) - known: raise PortError("unsupported_chat")
        payload = {k: v for k, v in final_form.items() if k in {"model", "messages", "stream", "max_tokens"}}
        context = self.runtime.policy.begin(verified_request, server_route=server_route, server_purpose=purpose)
        return self.runtime.accept(context, payload, request_id, session_id=session_id)

    def background(self, verified_request, final_form, *, purpose, request_id, session_id):
        # This deliberately reuses the manual route to prove route is insufficient.
        if purpose is ServerPurpose.MANUAL_CHAT: raise PortError("background_manual_claim_refused")
        return self.chat_entry(verified_request, final_form, server_route="/api/chat/completions", purpose=purpose,
                               request_id=request_id, session_id=session_id)
