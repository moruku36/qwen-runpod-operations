"""Plain-text lines that survive a log stream: status events and small artifacts.

The Pod side writes them to its stdout (and to files); Claude reads them back with the read-only
``GET /v2/pods/{id}/logs`` call. No inbound connection to the Pod and no extra credential is needed
by this channel itself. Artifacts are small (allowlisted report bundles) and carry a SHA256 so the
reader can prove the copy is complete and unmodified. Standard library only.
"""

from __future__ import annotations

import base64
import hashlib
import time
from pathlib import Path

EVENT = "QMC"
ART = "QMC-ART"
CHUNK = 1500
MAX_LINE = 240


def event_line(stage: str, message: str, ts: float | None = None) -> str:
    stamp = time.strftime("%H:%M:%S", time.gmtime(ts if ts is not None else time.time()))
    clean = " ".join(str(message).split())[:MAX_LINE]
    return f"{EVENT}|{stamp}Z|{stage}|{clean}"


def encode_artifact(path: Path, name: str | None = None) -> list[str]:
    data = Path(path).read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    b64 = base64.b64encode(data).decode()
    parts = [b64[i:i + CHUNK] for i in range(0, len(b64), CHUNK)] or [""]
    n = len(parts)
    name = name or Path(path).name
    return [f"{ART}|{name}|{sha}|{i}|{n}|{p}" for i, p in enumerate(parts, 1)]


def decode_artifacts(lines: list[str]) -> dict[str, dict]:
    """Rebuild artifacts from log lines. Returns {name: {"ok": bool, "data": bytes|None, "problem": str|None}}."""
    found: dict[str, dict] = {}
    for line in lines:
        if not line.startswith(ART + "|"):
            continue
        try:
            _, name, sha, idx, total, payload = line.rstrip("\n").split("|", 5)
            idx, total = int(idx), int(total)
        except ValueError:
            continue
        a = found.setdefault((name, sha), {"total": total, "parts": {}})
        a["parts"][idx] = payload
    out: dict[str, dict] = {}
    for (name, sha), a in found.items():
        missing = [i for i in range(1, a["total"] + 1) if i not in a["parts"]]
        if missing:
            out[name] = {"ok": False, "data": None, "problem": f"missing chunks {missing[:5]}"}
            continue
        try:
            data = base64.b64decode("".join(a["parts"][i] for i in range(1, a["total"] + 1)), validate=True)
        except ValueError:
            out[name] = {"ok": False, "data": None, "problem": "invalid base64"}
            continue
        if hashlib.sha256(data).hexdigest() != sha:
            out[name] = {"ok": False, "data": None, "problem": "sha256 mismatch"}
        else:
            out[name] = {"ok": True, "data": data, "problem": None, "sha256": sha}
    return out


def parse_events(lines: list[str]) -> list[dict]:
    out = []
    for line in lines:
        if line.startswith(EVENT + "|") and not line.startswith(ART + "|"):
            parts = line.split("|", 3)
            if len(parts) == 4:
                out.append({"time": parts[1], "stage": parts[2], "message": parts[3]})
    return out
