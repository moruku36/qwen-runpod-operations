#!/usr/bin/env python3
"""Plan / create / find / status / stop one RunPod trial pod via REST v2. No key is read or printed here.

  plan    read-only: availability, price, data centers, estimate, unique pod name, the exact create body
  create  PAID. Needs --approve LAUNCH-PAID-POD and the --name printed by `plan`. Run only with the owner's go-ahead.
  find    read-only: list pods whose name matches exactly (use after an uncertain create)
  status  read-only summary (allowlisted fields)
  stop    POST action=stop, then observe API EXITED. Console/log corroboration and Billing remain pending.

The estimate is an estimate. It is not an automatic stop and not a spending cap.
"""
import argparse
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from qmc_runpod import podapi

GPU_CHOICES = ["NVIDIA A100 80GB PCIe", "NVIDIA A100-SXM4-80GB"]
RANK = {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "NONE": 3}


def _hhmm(text: str, now: dt.datetime) -> dt.datetime:
    h, m = (int(x) for x in text.split(":"))
    return now.replace(hour=h, minute=m, second=0, microsecond=0)


def main(argv=None, api=None, now=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("plan", "create"):
        p = sub.add_parser(name)
        p.add_argument("--gpu", choices=GPU_CHOICES, help="default: the A100 80GB type with better current stock")
        p.add_argument("--launch-at", help="HH:MM JST (default: now)")
        p.add_argument("--stop-at", default="16:45", help="HH:MM JST stop target (default 16:45)")
        p.add_argument("--stop-lag-min", type=float, default=10.0)
        p.add_argument("--hold-hours", type=float, default=24.0, help="hours the Pod Volume is kept after stop")
        p.add_argument("--margin", type=float, default=0.20, help="tax/fee/rounding margin (assumption)")
        p.add_argument("--limit", type=float, default=10.0, help="your total limit in USD")
        p.add_argument("--dc", action="append", help="data center id (repeatable); default: best available")
        p.add_argument("--name", help="pod name (create: required, must be the one `plan` printed)")
        if name == "create":
            p.add_argument("--approve", required=True)
    for name in ("status", "stop"):
        sub.add_parser(name).add_argument("pod_id")
    sub.add_parser("find").add_argument("name")
    a = ap.parse_args(argv)
    api = api or podapi.PodApi()
    now = now or dt.datetime.now(podapi.JST)
    try:
        if a.cmd in ("plan", "create"):
            cands = [api.gpu_availability(g) for g in ([a.gpu] if a.gpu else GPU_CHOICES)]
            av = min(cands, key=lambda c: (RANK.get(c["availability"], 9), c["secure_price_per_hr"]))
            dcs = a.dc or [d["id"] for d in api.datacenters_with_gpu(av["id"])][:3]
            name = a.name or podapi.make_trial_name(now)
            if a.cmd == "create" and not a.name:
                print("create needs --name (the name printed by `plan`).", file=sys.stderr)
                return 2
            launch = _hhmm(a.launch_at, now) if a.launch_at else now
            est = podapi.estimate_session(av["secure_price_per_hr"], launch, _hhmm(a.stop_at, now),
                                          stop_lag_min=a.stop_lag_min, hold_hours_after_stop=a.hold_hours, margin=a.margin)
            body = podapi.build_create_body(name=name, gpu_id=av["id"], data_center_ids=dcs)
            print(json.dumps({"availability": av, "data_centers": dcs, "estimate": est, "limit_usd": a.limit,
                              "within_limit": est["total_usd"] <= a.limit, "body": body}, indent=2, ensure_ascii=False))
            if a.cmd == "plan":
                return 0
            pod = api.create_pod(body, approval=a.approve, estimate=est, limit_usd=a.limit)
            print(json.dumps(pod, indent=2))
        elif a.cmd == "find":
            print(json.dumps(api.find_pods_by_name(a.name), indent=2))
        elif a.cmd == "status":
            print(json.dumps(api.get_pod(a.pod_id), indent=2))
        else:
            try:
                print(json.dumps(api.stop_and_confirm(a.pod_id), indent=2))
            except podapi.StopFailed as exc:
                print(str(exc), file=sys.stderr)
                return 2
    except podapi.CreateUncertain as exc:
        print(str(exc), file=sys.stderr)
        return 3
    except podapi.PodApiError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
