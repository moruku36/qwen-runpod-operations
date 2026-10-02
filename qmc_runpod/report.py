"""Allowlist-only trial report: only known keys with plain values are ever written.

Pod API responses, environment variables, configs and chat text are never passed through; callers
copy individual fields in. ``validate`` rejects unknown keys and anything that looks like a secret.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import tarfile
from pathlib import Path

KEY_RE = re.compile(r"^[a-z0-9_]{1,48}$")
SECRETISH = re.compile(r"(?i)(bearer\s|api[_-]?key|passw|secret|jupyter|authorization|rpa_[a-z0-9]{8,})")
LONG_TOKEN = re.compile(r"[A-Za-z0-9+/_=-]{40,}")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
HEX40 = re.compile(r"^[0-9a-f]{40}$")

# section -> allowed keys. A key mapped to None is a free-form {name: number|bool|short str} dict.
SCHEMA = {
    "trial_id": "str",
    "created_at_jst": "str",
    "upstream": {"sha": "str", "notebook_blob": "str"},
    "llama_cpp": {"commit": "str", "version_line": "str"},
    "models": [{"role": "str", "file": "str", "revision": "str", "sha256_verified": "bool", "size_bytes": "num"}],
    "environment": {"gpu_name": "str", "vram_mib": "num", "driver": "str", "cuda_toolkit": "str",
                    "python": "str", "gradio": "str", "image": "str"},
    "settings": {"ctx": "num", "thinking": "bool", "web_search": "str", "asr_device": "str",
                 "asr_model": "str", "tts": "bool", "share": "bool", "chat_only": "bool", "profile": "str"},
    "timings_s": None,
    "metrics": None,
    "checks": None,
    "pod": {"id": "str", "gpu_type": "str", "data_center": "str", "price_per_hr": "num",
            "started_at": "str", "stopped_at": "str", "final_status": "str"},
    "cost_usd": None,
    "notes": ["str"],
}


def _scalar_ok(value, kind: str, path: str, errs: list[str]) -> None:
    if kind == "bool":
        ok = isinstance(value, bool)
    elif kind == "num":
        ok = isinstance(value, (int, float)) and not isinstance(value, bool)
    else:
        ok = isinstance(value, str) and len(value) <= 200
        if ok and (SECRETISH.search(value) or (LONG_TOKEN.search(value) and not (HEX64.match(value) or HEX40.match(value)))):
            errs.append(f"{path}: looks like a secret or token")
            return
    if not ok:
        errs.append(f"{path}: expected {kind}")


def _check(value, schema, path: str, errs: list[str]) -> None:
    if isinstance(schema, str):
        _scalar_ok(value, schema, path, errs)
    elif isinstance(schema, list):
        if not isinstance(value, list):
            errs.append(f"{path}: expected list")
            return
        for i, item in enumerate(value):
            _check(item, schema[0], f"{path}[{i}]", errs)
    elif isinstance(schema, dict):
        if not isinstance(value, dict):
            errs.append(f"{path}: expected object")
            return
        for k, v in value.items():
            if k not in schema:
                errs.append(f"{path}.{k}: key not in allowlist")
            else:
                _check(v, schema[k], f"{path}.{k}", errs)
    else:  # free-form {name: scalar}
        if not isinstance(value, dict):
            errs.append(f"{path}: expected object")
            return
        for k, v in value.items():
            if not KEY_RE.match(str(k)):
                errs.append(f"{path}.{k}: bad key name")
            elif not isinstance(v, (bool, int, float)):
                _scalar_ok(v, "str", f"{path}.{k}", errs)


def validate(report: dict) -> list[str]:
    errs: list[str] = []
    _check(report, SCHEMA, "report", errs)
    return errs


def write_report(report: dict, runs_dir: Path) -> Path:
    errs = validate(report)
    if errs:
        raise ValueError("report rejected:\n- " + "\n- ".join(errs))
    runs_dir = Path(runs_dir)
    runs_dir.mkdir(parents=True, exist_ok=True)
    out = runs_dir / f"report-{report['trial_id']}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return out


def make_bundle(files: list[Path], out_tar: Path) -> dict:
    """tar.gz of allowlisted report files + MANIFEST.json (name, size, sha256). Returns the manifest."""
    manifest = {"files": []}
    for f in files:
        data = Path(f).read_bytes()
        manifest["files"].append({"name": Path(f).name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    with tarfile.open(out_tar, "w:gz") as tar:
        for f in files:
            tar.add(f, arcname=Path(f).name)
        blob = json.dumps(manifest, indent=2).encode()
        info = tarfile.TarInfo("MANIFEST.json")
        info.size = len(blob)
        tar.addfile(info, io.BytesIO(blob))
    return manifest


def verify_bundle(tar_path: Path) -> list[str]:
    """Read-back check done after the bundle is stored outside the Pod. Returns a list of problems."""
    problems: list[str] = []
    with tarfile.open(tar_path, "r:gz") as tar:
        names = {m.name for m in tar.getmembers()}
        if "MANIFEST.json" not in names:
            return ["MANIFEST.json missing"]
        manifest = json.load(tar.extractfile("MANIFEST.json"))
        listed = {e["name"] for e in manifest["files"]}
        if names - {"MANIFEST.json"} != listed:
            problems.append("file list differs from manifest")
        for e in manifest["files"]:
            member = tar.extractfile(e["name"]) if e["name"] in names else None
            if member is None:
                problems.append(f"{e['name']}: missing")
                continue
            data = member.read()
            if len(data) != e["size"] or hashlib.sha256(data).hexdigest() != e["sha256"]:
                problems.append(f"{e['name']}: size/hash mismatch")
            elif e["name"].endswith(".json"):
                problems += [f"{e['name']}: {x}" for x in validate(json.loads(data))]
    return problems
