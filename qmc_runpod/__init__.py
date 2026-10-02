"""RunPod launcher for the chat-only Qwen Q8 app.

Thin layer over the upstream ``qmc`` package, which is checked out at a pinned commit
(see ``pins.py``). Nothing here modifies upstream files.
"""

__all__ = ["chat_ui", "launch", "layout", "llama", "models", "pins", "podapi", "report"]
