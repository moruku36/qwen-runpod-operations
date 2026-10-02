#!/usr/bin/env python3
"""Read a Pod's log stream (read-only API) and show only the QMC status events and system lines.
Artifacts sent with `pod_run.py emit` / `trial` are rebuilt, SHA256-checked and saved locally.

  python scripts/read_pod_logs.py <pod_id> [--tail 500] [--wait 8] [--out DIR]
"""
import argparse
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
    a = ap.parse_args(argv)
    api = api or podapi.PodApi()
    events = api.read_logs(a.pod_id, tail=a.tail, wait_s=a.wait)
    lines = [e.get("line", "") for e in events if e.get("source") == "container"]
    system = [e.get("line", "") for e in events if e.get("source") == "system"]
    for line in system[-5:]:
        print("system:", line[:160])
    for ev in logchannel.parse_events(lines)[-40:]:
        print(f"{ev['time']} [{ev['stage']}] {ev['message']}")
    print(f"({len(events)} log events; container lines: {len(lines)}; QMC events: {len(logchannel.parse_events(lines))})")
    rc = 0
    for name, art in logchannel.decode_artifacts(lines).items():
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
    return rc


if __name__ == "__main__":
    sys.exit(main())
