#!/usr/bin/env python3
"""Local bootstrap control. No Pod creation, start, stop or deletion.

python scripts/retry_run.py packet.json --validate
python scripts/retry_run.py packet.json --execute-local
"""
import argparse
import json
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from qmc_runpod import retry

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("packet", type=Path)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--validate", action="store_true")
    mode.add_argument("--execute-local", action="store_true")
    a = ap.parse_args()
    try:
        p = json.loads(a.packet.read_text(encoding="utf-8"))
        if a.validate:
            retry.validate_packet(p, REPO)
            print("packet validated; no execution or resource request")
            return 0
        return retry.execute(p, REPO)
    except Exception as exc:
        print(f"bootstrap blocked: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    sys.exit(main())
