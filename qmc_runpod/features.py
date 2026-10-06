"""Original-scope fixtures and evidence, separate from 8k performance.

Imports/downloads occur only in explicitly invoked runtime adapters. CPU tests use fakes.
No research agents, paid search provider, TTS or private conversation export.
"""
from __future__ import annotations
import hashlib
import json
import re
import sqlite3
import struct
import time
import zlib
from pathlib import Path

NAMES = ("ctx_32k", "image_understanding", "web_search", "asr", "history_restore", "ui_chat")
SEARCH_QUERY = "What is the Python programming language? Cite python.org."
ASR_REPO = "Systran/faster-whisper-small"
ASR_REVISION = "536b0662742c02347bc0e980a01041f333bce120"


def template() -> dict:
    return {n: {"status": "skipped", "reason": "not attempted", "evidence": {}, "urls": []} for n in NAMES}


def result(ok: bool, evidence: dict, reason: str, urls=None) -> dict:
    return {"status": "pass" if ok else "fail", "reason": reason,
            "evidence": evidence, "urls": urls or []}


def ui_receipt(path: Path, run_id: str) -> dict:
    if not path.is_file():
        return template()["ui_chat"]
    try:
        row = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return result(False, {"receipt_valid": False}, "UI receipt unreadable or malformed")
    if not isinstance(row, dict):
        return result(False, {"receipt_valid": False}, "UI receipt must be an object")
    ok = (row.get("run_id") == run_id and row.get("observer") == "owner"
          and row.get("input_to_display") is True and row.get("language") == "ja" and row.get("ctx") == 8192)
    return result(ok, {"owner_observed": ok, "ctx": 8192}, "owner receipt for authenticated Japanese UI at 8k")


def make_vision_fixture(path: Path) -> str:
    """Own synthetic PNG: red square, blue circle, black QMC 32 bitmap text. No image dependency."""
    width, height = 256, 128
    font = {"Q": [14,17,17,17,21,18,13], "M": [17,27,21,21,17,17,17],
            "C": [14,17,16,16,16,17,14], "3": [30,1,1,14,1,1,30], "2": [14,17,1,2,4,8,31]}
    rows = []
    for y in range(height):
        row = bytearray(b"\0")
        for x in range(width):
            color = (255,255,255)
            if 16 <= x < 80 and 12 <= y < 76:
                color = (255,0,0)
            if (x-176)**2 + (y-44)**2 <= 32**2:
                color = (0,0,255)
            if 90 <= y < 118:
                i, dx = divmod(x-48, 24)
                text = "QMC 32"
                if 0 <= i < len(text) and dx < 20 and text[i] in font and font[text[i]][(y-90)//4] & (1 << (4-dx//4)):
                    color = (0,0,0)
            row.extend(color)
        rows.append(bytes(row))
    def chunk(name, data):
        return struct.pack(">I",len(data))+name+data+struct.pack(">I",zlib.crc32(name+data)&0xffffffff)
    data = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR",struct.pack(">IIBBBBB",width,height,8,2,0,0,0))
    data += chunk(b"IDAT",zlib.compress(b"".join(rows)))+chunk(b"IEND",b"")
    path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def turn(app, sid, text, files=(), search="off") -> tuple[str, bool]:
    from qmc.controller import TurnOptions
    answer, error = [], False
    for ev in app.controller.handle(sid, text, list(files), TurnOptions(thinking=False, web_search=search, agent="off")):
        if ev.kind == "text":
            answer.append(str(ev.data))
        elif ev.kind == "error":
            error = True
    return "".join(answer), error


def context_check(app, post, get, memory) -> dict:
    """Real tokenizer measurement + explicit unload/reload; reject upstream OOM context degradation."""
    model = app.manager.get("chat")
    app.manager.unload("chat")
    model.ctx_size = 32768
    t0 = time.monotonic()
    app.manager.ensure("chat")
    loaded_s = time.monotonic()-t0
    slots = get("/slots")
    effective = min([s.get("n_ctx", 0) for s in slots] or [0])
    text = "BEGIN NEEDLE: violet-731.\n" + "ordinary neutral filler line.\n" * 7000
    # Count with actual llama tokenizer, then trim filler until input plus reserve fits.
    for _ in range(12):
        prompt = text + "\nEND NEEDLE: amber-842. Return both needle values only."
        count = len(post("/tokenize", {"content": prompt, "add_special": True})["tokens"])
        if count + 256 <= 32768:
            break
        text = text[:max(100, int(len(text)*(32000/count))-100)]
    if count < 28672 or count+256 > effective:
        return result(False, {"input_tokens": count, "effective_context": effective, "output_reserve": 256,
                              "reload_s": round(loaded_s,3), "oom": model.ctx_size != 32768}, "context/token budget insufficient")
    response = post("/completion", {"prompt": prompt, "n_predict": 256, "temperature": 0, "cache_prompt": False})
    answer = response.get("content", "")
    evaluated = response.get("tokens_evaluated", 0)
    evidence = {"requested_context": 32768, "effective_context": effective, "input_tokens": count,
                "tokens_evaluated": evaluated, "output_reserve": 256, "reload_s": round(loaded_s,3),
                "truncated": response.get("truncated", True), "oom": model.ctx_size != 32768,
                "needles_match": all(n in answer for n in ("violet-731", "amber-842")),
                "vram_used_mib": memory().get("used_mib", -1)}
    ok = effective == 32768 and model.ctx_size == 32768 and evaluated >= count-2 and not evidence["truncated"] and evidence["needles_match"]
    return result(ok, evidence, "long tokenizer-measured prompt; separate from 8k performance")


def run(app, fixture_dir: Path, *, allow_search: bool = False, audio: Path | None = None,
        transcript: str | None = None, deadline: float | None = None, cancel=None, on_result=None) -> dict:
    """Invoke only on the approved Pod. Every unattempted feature remains skipped with a reason."""
    from . import smoke
    out = template()
    def ready():
        return (deadline is None or time.time() < deadline) and not (cancel and cancel.exists())
    def attempt(name, fn):
        if not ready():
            out[name]["reason"] = "test deadline or cancellation reached"
            if on_result:
                on_result(name, out[name])
            return
        try:
            out[name] = fn()
        except Exception as exc:
            out[name] = result(False, {"error_type": type(exc).__name__}, "runtime attempt failed")
        if on_result:
            on_result(name, out[name])
    image = fixture_dir / "vision.png"
    sha = make_vision_fixture(image)
    def vision():
        sid = app.sessions.create_session("Synthetic vision fixture")
        answer, error = turn(app, sid, "Describe the colors and shapes and quote all text in English.", [str(image)])
        model = app.manager.get("chat")
        paths = getattr(model, "_paths", None)
        facts = [bool(re.search(p, answer, re.I)) for p in (r"red.*square|square.*red", r"blue.*circle|circle.*blue", r"QMC\s*32")]
        evidence = {"fixture_sha256": sha, "mmproj_configured": bool(paths and paths[1]),
                    "facts_matched": sum(facts), "error": error, "answer_sha256": hashlib.sha256(answer.encode()).hexdigest()}
        return result(not error and all(facts) and evidence["mmproj_configured"], evidence, "synthetic color/shape/text comparison")
    attempt("image_understanding", vision)
    def history():
        sid = app.sessions.create_session("Synthetic history fixture")
        answer, error = turn(app, sid, "Remember the synthetic marker orchard-571.")
        app.store.sync()
        # Read back in a new SQLite connection: persistence, not just an in-memory message list.
        con = sqlite3.connect(f"{app.cfg.local_db_path.resolve().as_uri()}?mode=ro", uri=True)
        try:
            rows = con.execute("SELECT role,content FROM messages WHERE session_id=? AND deleted=0 ORDER BY id", (sid,)).fetchall()
        finally:
            con.close()
        persisted = any(role == "assistant" and content == answer for role,content in rows)
        reply, error2 = turn(app, sid, "What synthetic marker did I ask you to remember?")
        return result(not error and not error2 and persisted and "orchard-571" in reply,
                      {"independent_db_read": persisted, "second_turn_match": "orchard-571" in reply}, "persisted synthetic history and second turn")
    attempt("history_restore", history)
    def search():
        from qmc.app import build_search
        previous = app.controller.search
        old_setting, old_provider = app.cfg.web_search, app.cfg.search_provider
        try:
            app.cfg.web_search = "on"; app.cfg.search_provider = "duckduckgo"
            app.controller.search = build_search(app.cfg)
            sid = app.sessions.create_session("Public search fixture")
            answer, error = turn(app, sid, SEARCH_QUERY, search="on")
            urls = []
            for msg in app.sessions.get_messages(sid):
                urls.extend(msg.meta.get("web_search", {}).get("urls", []))
            urls = list(dict.fromkeys(u for u in urls if u.startswith("https://") and len(u) <= 200))
            linked = any(u in answer for u in urls)
            return result(not error and bool(urls) and linked, {"retrieval_urls": len(urls), "answer_linked": linked,
                          "provider": "duckduckgo", "query_fixture": "python_public", "search_returned_off": True}, "public query sent through DDGS; no agent", urls)
        finally:
            app.controller.search = previous
            app.cfg.web_search, app.cfg.search_provider = old_setting, old_provider
    if allow_search:
        attempt("web_search", search)
    else:
        out["web_search"]["reason"] = "explicit public-provider transmission flag required"
    if audio is not None and transcript:
        def asr():
            from huggingface_hub import snapshot_download
            from qmc.asr import WhisperASR
            t0 = time.monotonic()
            model_dir = snapshot_download(ASR_REPO, revision=ASR_REVISION, allow_patterns=["model.bin", "config.json", "tokenizer.json", "vocabulary.*"])
            download_s = time.monotonic()-t0
            got = WhisperASR(model_dir, "cpu").transcribe(audio)
            normalize = lambda s: re.sub(r"[\W_]", "", s, flags=re.UNICODE).lower()
            return result(normalize(got) == normalize(transcript), {"model_revision": ASR_REVISION,
                          "download_s": round(download_s,3), "fixture_sha256": hashlib.sha256(audio.read_bytes()).hexdigest(),
                          "transcript_match": normalize(got) == normalize(transcript), "device": "cpu"}, "known licensed transcript comparison")
        attempt("asr", asr)
    else:
        out["asr"]["reason"] = "licensed known-transcript speech fixture missing"
    def context():
        import requests
        model = app.manager.get("chat")
        base = model.client.base_url.removesuffix("/v1")
        if not base.startswith("http://127.0.0.1:"):
            raise ValueError("local llama server required")
        def call(path, data=None):
            resp = requests.get(base+path, headers=model.client._headers(), timeout=15) if data is None else requests.post(base+path, json=data, headers=model.client._headers(), timeout=300)
            resp.raise_for_status(); return resp.json()
        return context_check(app, lambda path,data: call(path,data), call, smoke.gpu_memory_mib)
    attempt("ctx_32k", context)
    out["ui_chat"]["reason"] = "owner must verify authenticated Japanese UI input-to-display; backend calls do not prove UI"
    return out
