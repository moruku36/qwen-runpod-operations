"""Start the chat-only app with strict preflight checks. Fails closed; never falls back."""

from __future__ import annotations

import os
import socket
from pathlib import Path

from . import pins


class LaunchError(RuntimeError):
    pass


def port_is_free(port: int, host: str = "0.0.0.0") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
        except OSError:
            return False
    return True


def preflight(cfg, *, require_auth: bool = True) -> None:
    """Raise LaunchError unless it is safe to start. Does not print or return secrets."""
    problems = []
    if require_auth and not (cfg.auth_user and cfg.auth_password):
        problems.append("QMC_AUTH_USER と QMC_AUTH_PASSWORD の両方が必要です（片方・両方なしでは起動しません）")
    if cfg.share:
        problems.append("share=True は許可しません")
    if not cfg.chat_only:
        problems.append("chat_only=True が必要です")
    if not cfg.mock:
        if cfg.chat.remote_base_url:
            problems.append("QMC_CHAT_BASE_URL（外部のchat API）は使いません。同じPodのllama-serverだけを使います")
        if cfg.chat.host != pins.LLAMA_HOST:
            problems.append(f"llama-server は {pins.LLAMA_HOST} 限定です（現在: {cfg.chat.host}）")
    if not port_is_free(cfg.server_port, "0.0.0.0"):
        problems.append(f"ポート {cfg.server_port} は使用中です（別ポートへ自動変更はしません）")
    if problems:
        raise LaunchError("起動を中止しました:\n- " + "\n- ".join(problems))


def make_config(*, mock: bool = False, profile: str | None = None, host: str | None = None, port: int | None = None):
    from qmc.config import load_config

    cfg = load_config(mock=mock, chat_only=True, share=False, server_port=port or pins.GRADIO_PORT)
    cfg.web_search = os.environ.get("QMC_WEB_SEARCH", "off")
    if profile:
        cfg.gpu_profile_override = profile
    if host:
        cfg.server_host = host
    cfg.chat.host = pins.LLAMA_HOST
    cfg.chat.port = pins.LLAMA_PORT
    return cfg


def launch(*, mock: bool = False, profile: str | None = None, host: str | None = None,
           port: int | None = None, model_paths: tuple[Path, Path | None] | None = None):
    """Build the app and start Gradio (non-blocking). Returns the upstream ``App``."""
    from qmc.app import build_app

    from . import chat_ui

    cfg = make_config(mock=mock, profile=profile, host=host, port=port)
    preflight(cfg)
    app = build_app(cfg)
    chat = app.manager.get("chat")
    if model_paths is not None and hasattr(chat, "_paths"):
        chat._paths = model_paths  # load exactly the verified files, not "main" of the HF repo
    demo = chat_ui.build_ui(app)
    demo.queue(default_concurrency_limit=4)
    demo.launch(
        server_name=cfg.server_host, server_port=cfg.server_port, share=False,
        auth=(cfg.auth_user, cfg.auth_password), prevent_thread_lock=True, inline=False, quiet=True,
        allowed_paths=[str(cfg.data_dir.resolve())],
    )
    app.demo = demo
    return app


def stop_app(app) -> None:
    """Stop the web app and the model process. Does NOT stop or delete the Pod."""
    for fn in (lambda: app.demo.close(), lambda: app.manager.unload_all(),
               lambda: (app.store.sync(), app.store.close())):
        try:
            fn()
        except Exception:  # noqa: BLE001, S110 - best effort cleanup
            pass
