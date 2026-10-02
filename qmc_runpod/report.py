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
CHECK_NAMES = (
    "upstream_pin", "ops_commit_clean", "lock_installed", "llama_built", "models_verified", "app_launch",
    "auth_enforced", "first_response", "warm_runs", "perf_criteria", "image_understanding", "web_search", "asr",
    "ctx_32k", "history_restore", "release_reload", "report_exported", "kernel_registered", "ui_chat",
)
CHECK_VALUES = ("pass", "fail", "skipped")

SCHEMA = {
    "trial_id": "str",
    "created_at_jst": "str",
    "source": {"ops_commit": "str", "ops_tree_clean": "bool", "lock_sha256": "str"},
    "upstream": {"sha": "str", "notebook_blob": "str"},
    "llama_cpp": {"commit": "str", "version_line": "str", "cuda_archs": "str", "runtime_tag": "str",
                  "build_type": "str", "build_seconds": "num", "download_seconds": "num", "built_this_run": "bool"},
    "models": [{"role": "str", "file": "str", "revision": "str", "sha256_verified": "bool", "size_bytes": "num"}],
    "environment": {"gpu_name": "str", "vram_mib": "num", "driver": "str", "cuda_toolkit": "str",
                    "cuda_driver_api": "str", "python": "str", "gradio": "str", "image_tag": "str",
                    "image_digest": "str"},
    "settings": {"ctx": "num", "thinking": "bool", "web_search": "str", "asr_device": "str",
                 "asr_model": "str", "tts": "bool", "share": "bool", "chat_only": "bool", "profile": "str"},
    "timings_s": None,
    "metrics": None,
    "checks": None,
    "pod": {"id": "str", "gpu_type": "str", "data_center": "str", "price_per_hr": "num",
            "started_at": "str", "stopped_at": "str", "final_status": "str"},
    "cost_usd": None,
    "notes": ["str"],
    "execution_mode": "str",
    "features": {name: {"status": "str", "reason": "str", "evidence": None,
                        "urls": ["str"]} for name in ("ctx_32k", "image_understanding", "web_search", "asr", "history_restore", "ui_chat")},
}


def _scalar_ok(value, kind: str, path: str, errs: list[str]) -> None:
    if kind == "nullable_num":
        if value is None:
            return
        kind = "num"
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
            if path == "report.metrics" and k == "warm_samples":
                _check(v, [{"phase": "str", "first_delta_s": "nullable_num", "total_s": "num",
                            "stream_deltas": "num", "finish_reason": "str", "max_tokens": "num", "error": "bool"}],
                       path + ".warm_samples", errs)
                continue
            if not KEY_RE.match(str(k)):
                errs.append(f"{path}.{k}: bad key name")
            elif not isinstance(v, (bool, int, float)):
                _scalar_ok(v, "str", f"{path}.{k}", errs)


def checks_template() -> dict:
    """Every known check, all "skipped" until something marks it. Nothing is a pass by default."""
    return dict.fromkeys(CHECK_NAMES, "skipped")


def mark(checks: dict, name: str, ok: bool | None) -> None:
    """ok=True -> pass, False -> fail, None -> skipped."""
    if name not in CHECK_NAMES:
        raise KeyError(name)
    checks[name] = "skipped" if ok is None else ("pass" if ok else "fail")


def validate(report: dict) -> list[str]:
    errs: list[str] = []
    _check(report, SCHEMA, "report", errs)
    checks = report.get("checks")
    if isinstance(checks, dict):
        for name in CHECK_NAMES:
            if name not in checks:
                errs.append(f"report.checks.{name}: missing (must be pass, fail or skipped)")
        for name, value in checks.items():
            if name not in CHECK_NAMES:
                errs.append(f"report.checks.{name}: not a known check")
            elif value not in CHECK_VALUES:
                errs.append(f"report.checks.{name}: must be one of {CHECK_VALUES}")
    elif "checks" in report:
        errs.append("report.checks: expected object")
    else:
        errs.append("report.checks: missing")
    feature_rows = report.get("features", {})
    for name, row in (feature_rows if isinstance(feature_rows, dict) else {}).items():
        if not isinstance(row, dict):
            continue
        if row.get("status") not in CHECK_VALUES:
            errs.append(f"report.features.{name}: invalid status")
        if row.get("status") == "pass" and not row.get("evidence"):
            errs.append(f"report.features.{name}: pass needs evidence")
        if isinstance(checks, dict) and checks.get(name) != row.get("status"):
            errs.append(f"report.features.{name}: differs from check status")
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


def verify_bundle(tar_path: Path, *, expected_run_id: str | None = None,
                  required_checks: tuple[str, ...] = ()) -> list[str]:
    """Read-back check done after the bundle is stored outside the Pod. Returns a list of problems."""
    problems: list[str] = []
    try:
        tar = tarfile.open(tar_path, "r:gz")  # noqa: SIM115 - closed below
    except (tarfile.TarError, OSError, EOFError):
        return ["not a readable tar.gz"]
    with tar:
        try:
            members = tar.getmembers()
        except (tarfile.TarError, OSError, EOFError):
            return ["incomplete tar members"]
        names = {m.name for m in members}
        if len(names) != len(members) or any(not m.isfile() or Path(m.name).name != m.name or "\\" in m.name
                                           or m.size > 5_000_000 for m in members):
            return ["duplicate, unsafe or oversized bundle member"]
        if "MANIFEST.json" not in names:
            return ["MANIFEST.json missing"]
        try:
            manifest = json.load(tar.extractfile("MANIFEST.json"))
            entries = manifest["files"]
            if not entries or len({e["name"] for e in entries}) != len(entries):
                return ["empty or duplicate manifest"]
        except (ValueError, KeyError, TypeError):
            return ["invalid manifest"]
        listed = {e["name"] for e in manifest["files"]}
        if names - {"MANIFEST.json"} != listed:
            problems.append("file list differs from manifest")
        report_count = 0
        for e in entries:
            member = tar.extractfile(e["name"]) if e["name"] in names else None
            if member is None:
                problems.append(f"{e['name']}: missing")
                continue
            data = member.read()
            if len(data) != e.get("size") or hashlib.sha256(data).hexdigest() != e.get("sha256"):
                problems.append(f"{e['name']}: size/hash mismatch")
            elif e["name"].endswith(".json"):
                try:
                    rep = json.loads(data)
                    problems += [f"{e['name']}: {x}" for x in validate(rep)]
                    report_count += 1
                    if expected_run_id is not None and rep.get("trial_id") != expected_run_id:
                        problems.append("unexpected report run ID")
                    for name in required_checks:
                        if rep.get("checks", {}).get(name) != "pass":
                            problems.append(f"required check not passed: {name}")
                    if required_checks and rep.get("execution_mode") != "real_gpu":
                        problems.append("CPU/mock report cannot satisfy real-run acceptance")
                except (ValueError, TypeError, AttributeError):
                    problems.append("invalid report JSON")
            else:
                problems.append("file not in report allowlist")
        if report_count != 1:
            problems.append("exactly one report required")
    return problems
