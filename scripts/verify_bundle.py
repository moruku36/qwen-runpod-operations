#!/usr/bin/env python3
"""Read-back check for a report bundle stored outside the Pod: manifest hashes + allowlist schema."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from qmc_runpod import report  # noqa: E402

problems = report.verify_bundle(Path(sys.argv[1]))
print("OK" if not problems else "\n".join(problems))
sys.exit(1 if problems else 0)
