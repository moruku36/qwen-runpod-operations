"""Chat-only Gradio UI for Gradio 6.

Upstream ``qmc.ui_chat`` still passes ``type="messages"`` to ``gr.Chatbot``; Gradio 6 removed that
argument (messages format is the only one) and raises TypeError. Rather than edit upstream, the
argument is dropped while the upstream UI is built.
"""

from __future__ import annotations

from unittest import mock

import gradio as gr


def build_ui(app):
    from qmc import ui_chat

    real = gr.Chatbot

    def chatbot(*args, **kwargs):
        kwargs.pop("type", None)
        return real(*args, **kwargs)

    with mock.patch.object(gr, "Chatbot", chatbot):
        return ui_chat.build_ui(app)
