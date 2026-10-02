#!/usr/bin/env python3
"""Plan / create / status / stop one RunPod trial pod via REST v2. No key is read or printed here.

  plan    read-only: availability, price, data centers, cost estimate, the exact create body
  create  paid. Needs --approve LAUNCH-PAID-POD. Do not run without the owner's go-ahead.
  status  read-only summary (allowlisted fields)
  stop    POST action=stop, then GET until EXITED. No terminate exists in this tool.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from qmc_runpod import podapi

GPU_CHOICES = ["NVIDIA A100 80GB PCIe", "NVIDIA A100-SXM4-80GB"]


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("plan", "create"):
        p = sub.add_parser(name)
        p.add_argument("--gpu", choices=GPU_CHOICES, help="default: the A100 80GB type with better current stock")
        p.add_argument("--hours", type=float, default=2.0)
        p.add_argument("--budget", type=float, default=10.0)
        p.add_argument("--name", default="qwen-trial")
        p.add_argument("--dc", action="append", help="data center id (repeatable); default: best available")
        if name == "create":
            p.add_argument("--approve", required=True)
    for name in ("status", "stop"):
        sub.add_parser(name).add_argument("pod_id")
    a = ap.parse_args(argv)
    api = podapi.PodApi()
    if a.cmd in ("plan", "create"):
        rank = {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "NONE": 3}
        cands = [api.gpu_availability(g) for g in ([a.gpu] if a.gpu else GPU_CHOICES)]
        av = min(cands, key=lambda c: (rank.get(c["availability"], 9), c["secure_price_per_hr"]))
        a.gpu = av["id"]
        dcs = a.dc or [d["id"] for d in api.datacenters_with_gpu(a.gpu)][:3]
        body = podapi.build_create_body(name=a.name, gpu_id=a.gpu, data_center_ids=dcs)
        est = podapi.estimate_cost(av["secure_price_per_hr"], a.hours)
        print(json.dumps({"availability": av, "data_centers": dcs, "estimate_usd": est, "body": body}, indent=2))
        if a.cmd == "plan":
            return 0
        pod = api.create_pod(body, approval=a.approve, price_per_hr=av["secure_price_per_hr"], hours=a.hours, budget_usd=a.budget)
        print(json.dumps(pod, indent=2))
    elif a.cmd == "status":
        print(json.dumps(api.get_pod(a.pod_id), indent=2))
    else:
        try:
            print(json.dumps(api.stop_and_confirm(a.pod_id), indent=2))
        except podapi.StopFailed as exc:
            print(str(exc), file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
