#!/usr/bin/env python3
"""Read a Pod's log stream (read-only API) and show only the QMC status events and system lines.
Artifacts sent with `pod_run.py emit` / `trial` are rebuilt, SHA256-checked and saved locally.

  python scripts/read_pod_logs.py <pod_id> [--tail 500] [--wait 8] [--out DIR]
"""
import argparse
import json
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from qmc_runpod import logchannel, podapi, report


def main(argv=None, api=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("pod_id")
    ap.add_argument("--tail", type=int, default=500)
    ap.add_argument("--wait", type=float, default=8.0)
    ap.add_argument("--out", type=Path, default=Path("pod-artifacts"))
    ap.add_argument("--run-id")
    ap.add_argument("--expect-event", action="append", default=[], help="exact stage:message")
    ap.add_argument("--expect-file", action="append", default=[], help="name=full_sha256")
    a = ap.parse_args(argv)
    api = api or podapi.PodApi()
    events = api.read_logs(a.pod_id, tail=a.tail, wait_s=a.wait)
    lines = [e.get("line", "") for e in events if e.get("source") == "container"]
    system = [e.get("line", "") for e in events if e.get("source") == "system"]
    for line in system[-5:]:
        print("system:", line[:160])
    parsed = logchannel.parse_events(lines, run_id=a.run_id)
    for ev in parsed[-40:]:
        print(f"{ev['time']} [{ev['stage']}] {ev['message']}")
    print(f"({len(events)} log events; container lines: {len(lines)}; matching QMC events: {len(parsed)})")
    rc = 0 if parsed else 1
    artifacts = logchannel.decode_artifacts(lines, run_id=a.run_id)
    for expected in a.expect_event:
        if expected not in {e['stage'] + ':' + e['message'] for e in parsed}:
            print("expected event missing:", expected)
            rc = 1
    for expected in a.expect_file:
        name, sha = expected.split("=", 1)
        if not artifacts.get(name, {}).get("ok") or artifacts[name].get("sha256") != sha:
            print("expected file/hash missing:", name)
            rc = 1
    for name, art in artifacts.items():
        if not art["ok"]:
            print(f"artifact {name}: NOT usable ({art['problem']})")
            rc = 1
            continue
        a.out.mkdir(parents=True, exist_ok=True)
        dest = a.out / name
        dest.write_bytes(art["data"])
        msg = f"artifact {name}: ok sha256={art['sha256'][:12]}… saved to {dest}"
        if name.endswith(".tar.gz"):
            problems = report.verify_bundle(dest)
            msg += " | bundle check: " + ("OK" if not problems else "; ".join(problems))
            rc = rc or (1 if problems else 0)
        print(msg)
    if a.run_id:
        if not a.expect_event or not a.expect_file:
            print("strict receipt requires expected events and full file hashes")
            rc = 1
        a.out.mkdir(parents=True, exist_ok=True)
        receipt = {"run_id": a.run_id, "verified": rc == 0,
                   "expected_events": a.expect_event, "expected_files": a.expect_file,
                   "transport": getattr(api, "last_log_read", {"state": "unknown"})}
        (a.out / "receipt.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    return rc


if __name__ == "__main__":
    sys.exit(main())
