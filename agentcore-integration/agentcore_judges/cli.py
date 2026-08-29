"""agentcore-judges — inspect, validate and deploy the suite on AgentCore.

Offline (PyYAML only — no AWS account, no credentials, no network):
    agentcore-judges list
    agentcore-judges check-drift
    agentcore-judges plan --lambda-arn ARN
    agentcore-judges render --judge signal-density
    agentcore-judges verify results.jsonl

Needs boto3 (validate also needs it, but makes no API call):
    agentcore-judges validate --lambda-arn ARN
    agentcore-judges provision --lambda-arn ARN --apply
    agentcore-judges online-config --tier critical --log-group LG --service SVC \
        --role ROLE_ARN [--apply]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import LEVELS, MAX_EVALUATORS_PER_CONFIG, UNIT_TO_LEVEL
from .judge import build_request
from .naming import to_evaluator_name
from .provision import (LambdaTarget, TIERS, apply, online_config_payload, plan,
                        read_lock, validate_request)
from .spec import check_drift, deployable, load_rubrics
from .verify import verify


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="agentcore-judges", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list"); sub.add_parser("check-drift")

    r = sub.add_parser("render"); r.add_argument("--judge", required=True)

    for name in ("plan", "validate", "provision"):
        p = sub.add_parser(name)
        p.add_argument("--lambda-arn", required=True)
        p.add_argument("--timeout", type=int, default=300)
        p.add_argument("--traced", action="store_true")
        if name == "provision":
            p.add_argument("--region"); p.add_argument("--apply", action="store_true")

    o = sub.add_parser("online-config")
    o.add_argument("--tier", choices=sorted(TIERS), required=True)
    o.add_argument("--log-group", action="append", required=True, dest="log_groups")
    o.add_argument("--service", action="append", required=True, dest="services")
    o.add_argument("--role", required=True); o.add_argument("--region")
    o.add_argument("--enable", action="store_true", help="enableOnCreate")
    o.add_argument("--apply", action="store_true")

    v = sub.add_parser("verify"); v.add_argument("path", type=Path)

    a = ap.parse_args(argv)
    try:
        return _run(a)
    except (ValueError, FileNotFoundError, KeyError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


def _run(a) -> int:
    if a.cmd == "list":
        rs = load_rubrics(); dep = {x.id for x in deployable(rs)}
        print(f"{'judge':30} {'unit':13} {'level':9} {'evaluatorName':28} deployed")
        print("-" * 92)
        for x in rs:
            print(f"{x.id:30} {x.unit:13} {UNIT_TO_LEVEL[x.unit]:9} "
                  f"{to_evaluator_name(x.id):28} {'yes' if x.id in dep else 'no (variant)'}")
        by = {}
        for x in rs:
            if x.id in dep:
                by[UNIT_TO_LEVEL[x.unit]] = by.get(UNIT_TO_LEVEL[x.unit], 0) + 1
        print(f"\n{len(dep)} deploy: " + ", ".join(f"{v}x {k}" for k, v in sorted(by.items())))
        for t, cfg in TIERS.items():
            print(f"  tier {t:12} sampling={cfg['sampling']:>5}%  {len(cfg['judges'])} judges"
                  f"  (cap {MAX_EVALUATORS_PER_CONFIG})")
        return 0

    if a.cmd == "check-drift":
        probs = check_drift()
        for p in probs:
            print(f"DRIFT {p}")
        print("in sync with ../judges" if not probs else f"{len(probs)} drifted")
        return 1 if any("changed" in p or "deleted" in p for p in probs) else 0

    if a.cmd == "render":
        rb = load_rubrics([a.judge])[0]
        req = build_request(rb, "[BEGIN TRANSCRIPT]\n<transcript>\n[END TRANSCRIPT]",
                            "the assistant turn marked `>>> TARGET`")
        print(json.dumps({k: v for k, v in req.items() if k != "system"}, indent=2)[:1400])
        print(f"\n--- system: {len(req['system'])} blocks, "
              f"{sum(len(b['text']) for b in req['system'])} chars, "
              f"cache_control on block 2 ---")
        return 0

    if a.cmd in ("plan", "validate", "provision"):
        tgt = LambdaTarget(arn=a.lambda_arn, timeout_seconds=a.timeout)
        if a.cmd == "plan":
            print(json.dumps(plan(tgt, prefer_traced=a.traced), indent=2))
            return 0
        if a.cmd == "validate":
            bad = 0
            for p in plan(tgt, prefer_traced=a.traced):
                probs = validate_request("CreateEvaluator", p)
                bad += len(probs)
                status = "ok" if not probs else "; ".join(probs)
                print(f"  {p['evaluatorName']:30} {status}")
            print(f"\n{bad} problem(s)")
            return 1 if bad else 0
        res = apply(tgt, region=a.region, prefer_traced=a.traced, dry_run=not a.apply)
        for act in res["actions"]:
            print(f"  {act['action']:9} {act['evaluatorName']}")
        print(f"\n{'APPLIED' if a.apply else 'DRY RUN — nothing was sent'}")
        return 0

    if a.cmd == "online-config":
        payload = online_config_payload(a.tier, a.log_groups, a.services, a.role,
                                        enable_on_create=a.enable)
        probs = validate_request("CreateOnlineEvaluationConfig", payload)
        if probs:
            for p in probs:
                print(f"  invalid: {p}", file=sys.stderr)
            return 1
        if not a.apply:
            print(json.dumps(payload, indent=2))
            print("\nDRY RUN. re-run with --apply", file=sys.stderr)
            return 0
        import boto3
        c = boto3.client("bedrock-agentcore-control", region_name=a.region)
        res = c.create_online_evaluation_config(**payload)
        print(f"created: {res.get('onlineEvaluationConfigId')}")
        return 0

    if a.cmd == "verify":
        rubrics = {x.id: x for x in load_rubrics()}
        bad = 0
        for line in a.path.read_text().splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            res = verify(rubrics[rec["judge_id"]], rec.get("explanation"),
                         rec.get("label"), transcript=rec.get("transcript"))
            if not res.ok:
                bad += 1
                print(f"FAIL {rec['judge_id']}: {res.errors[0]}")
            for w in res.warnings:
                print(f"warn {rec['judge_id']}: {w}")
        print(f"\n{bad} invalid")
        return 1 if bad else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
