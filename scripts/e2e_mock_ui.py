#!/usr/bin/env python3
"""Manual CPU check: launch the real chat-only UI in mock mode and drive it in Chromium.

Needs playwright (not part of the Pod lock) and the pinned upstream checkout. Run:
  QMC_UPSTREAM_DIR=<checkout> python scripts/e2e_mock_ui.py
"""
import os
import socket
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UP = Path(os.environ.get("QMC_UPSTREAM_DIR", "/home/user/moruku36/qwen-multimodal-colab"))
sys.path[:0] = [str(ROOT), str(UP / "src")]

tmp = tempfile.mkdtemp()
os.environ.update(QMC_DATA_DIR=f"{tmp}/data", QMC_LOCAL_DB=f"{tmp}/db/h.db", QMC_WEB_SEARCH="off",
                  QMC_AUTH_USER="e2e-user", QMC_AUTH_PASSWORD="e2e-pass-" + os.urandom(4).hex())

from qmc_runpod import launch

s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
app = launch.launch(mock=True, host="127.0.0.1", port=port)
try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=os.environ.get("CHROME", "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"),
                              args=["--no-sandbox"])
        page = b.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
        assert page.locator("input[name=username], input[type=text]").count() >= 1, "login form missing"
        page.fill("input[type=text]", os.environ["QMC_AUTH_USER"])
        page.fill("input[type=password]", os.environ["QMC_AUTH_PASSWORD"])
        page.keyboard.press("Enter")
        page.wait_for_selector("text=Qwen Q8 Chat", timeout=30000)
        box = page.locator("textarea").first
        box.fill("こんにちは")
        box.press("Enter")
        page.wait_for_selector(".message.bot, [data-testid=bot]", timeout=60000)
        page.wait_for_timeout(1500)
        body = page.inner_text("body")
        assert "こんにちは" in body, "user message not rendered"
        print("bot messages:", page.locator(".message.bot, [data-testid=bot]").count())
        print("page errors:", errors)
        page.screenshot(path=os.environ.get("SHOT", f"{tmp}/ui.png"))
        print("screenshot:", os.environ.get("SHOT", f"{tmp}/ui.png"))
        b.close()
finally:
    launch.stop_app(app)
