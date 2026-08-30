"""native-judges — inspect, validate and deploy the NATIVE llmAsAJudge suite."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import RATING_SCALE, UNIT_TO_LEVEL
from .naming import to_evaluator_name
from .provision import (ModelConfig, TIERS, online_config_payload, plan, read_lock,
                        validate_request, write_lock)
from .score import (DEFAULT_EFFORT, DEFAULT_MODEL, api_key_present, band_shortfalls,
                    confirm, default_judges, dry_run_report, metrics_for, plan_prompts,
                    prepare_out, render_report, run_live, write_results)
from .spec import check_drift, deployable, load_rubrics
from .transform import transform


def _model(a) -> ModelConfig:
    extra = {}
    if getattr(a, "thinking", False):
        # Where Anthropic extended thinking would go on the Bedrock shape.
        # UNVERIFIED — see docs/LIMITS.md before trusting it.
        extra = {"thinking": {"type": "adaptive"}}
    return ModelConfig(model_id=a.model, max_tokens=a.max_tokens,
                       temperature=a.temperature, additional_fields=extra)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="native-judges", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list"); sub.add_parser("check-drift"); sub.add_parser("diff")

    t = sub.add_parser("show", help="print one judge's rendered instructions")
    t.add_argument("--judge", required=True); t.add_argument("--full", action="store_true")

    for n in ("plan", "validate", "provision"):
        p = sub.add_parser(n)
        p.add_argument("--model", default="anthropic.claude-opus-4-5-20251101-v1:0")
        p.add_argument("--max-tokens", type=int, default=8000)
        p.add_argument("--temperature", type=float, default=0.0)
        p.add_argument("--thinking", action="store_true",
                       help="add additionalModelRequestFields.thinking (UNVERIFIED)")
        p.add_argument("--traced", action="store_true")
        if n == "provision":
            p.add_argument("--region"); p.add_argument("--apply", action="store_true")

    s = sub.add_parser("score", help="score the TRANSFORMED prompts against data/testsets/",
                       description="Score the transformed prompts — not the raw rubrics — "
                                   "against the labelled test sets. Simulates how AgentCore "
                                   "presents a trace; see native_judges/score.py.")
    s.add_argument("--judge", action="append", dest="judges",
                   help="repeatable; default is every deployable judge (10 — a variant "
                        "and its base are mutually exclusive, so capability-honesty-traced "
                        "is out by default). Naming a variant here scores it anyway: "
                        "`--judge capability-honesty-traced` is how you compare the pair")
    s.add_argument("--limit", type=int, default=6,
                   help="samples per judge, balanced across the three expected_score "
                        "bands; 0 means every item the test set holds — 60 as shipped "
                        "(default: 6). A band with too few items is under-drawn and the "
                        "shortfall is printed")
    s.add_argument("--k", type=int, default=1,
                   help="samples per item (default: 1). AgentCore's native path runs the "
                        "evaluator once per trace with no repetition, so k>1 measures this "
                        "prompt's self-consistency, not the deployed behaviour")
    s.add_argument("--dry-run", action="store_true",
                   help="build every prompt, call nothing; the default with no API key")
    s.add_argument("--out", type=Path, help="write per-item results as JSONL")
    s.add_argument("--seed", type=int, default=0)
    s.add_argument("--yes", action="store_true", help="skip the cost confirmation")
    s.add_argument("--model", default=DEFAULT_MODEL)
    s.add_argument("--effort", default=DEFAULT_EFFORT,
                   choices=("low", "medium", "high", "xhigh", "max"))

    o = sub.add_parser("online-config")
    o.add_argument("--tier", choices=sorted(TIERS), required=True)
    o.add_argument("--log-group", action="append", required=True, dest="log_groups")
    o.add_argument("--service", action="append", required=True, dest="services")
    o.add_argument("--role", required=True); o.add_argument("--region")
    o.add_argument("--enable", action="store_true"); o.add_argument("--apply", action="store_true")

    a = ap.parse_args(argv)
    try:
        return _run(a)
    except (ValueError, FileNotFoundError, KeyError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


def _run(a) -> int:
    if a.cmd == "list":
        rs = load_rubrics(); dep = {r.id for r in deployable(rs)}
        print(f"{'judge':28} {'level':9} {'instructions':>13} {'rewrites':>9} deployed")
        print("-" * 76)
        for r in rs:
            t = transform(r)
            print(f"{r.id:28} {t.level:9} {t.chars:>13,} {t.rewrites:>9} "
                  f"{'yes' if r.id in dep else 'no (variant)'}")
        print(f"\nrating scale: " + ", ".join(f"{x['label']}={x['value']}" for x in RATING_SCALE))
        for k, c in TIERS.items():
            print(f"  tier {k:12} sampling={c['sampling']:>5}%  {len(c['judges'])} judges")
        return 0

    if a.cmd == "check-drift":
        probs = check_drift()
        real = [p for p in probs if "changed" in p or "deleted" in p]
        if not probs:
            print("sources in sync with ../judges")
        elif not real:
            print(f"no reference to compare against ({probs[0]})")
            print("this folder is standalone and still works; drift cannot be checked here")
        else:
            for p in real:
                print(f"DRIFT {p}")
            print(f"{len(real)} drifted")
        return 1 if real else 0

    if a.cmd == "diff":
        print("What the native path strips from each rubric, and why.\n")
        for r in deployable(load_rubrics()):
            t = transform(r)
            print(f"{r.id}")
            for d in t.removed:
                print(f"    removed  {d}")
            print(f"    rewrote  {t.rewrites} transcript-marker references")
            if t.residual_markers:
                print(f"    WARNING  residual: {t.residual_markers}")
        print("\nEvery rubric also loses: evidence spans, per-check span quotes, "
              "confidence,\nthe per-judge extra fields, and k=5 sampling. "
              "See docs/LIMITS.md.")
        return 0

    if a.cmd == "show":
        t = transform(load_rubrics([a.judge])[0])
        print(t.instructions if a.full else t.instructions[:2500] + "\n\n[... --full for all]")
        print(f"\n--- {t.chars:,} chars, level={t.level}, {t.rewrites} marker rewrites ---")
        return 0

    if a.cmd in ("plan", "validate", "provision"):
        m = _model(a)
        ps = plan(m, prefer_traced=a.traced)
        if a.cmd == "plan":
            print(json.dumps(ps, indent=2)[:4000]); return 0
        if a.cmd == "validate":
            bad = 0
            for p in ps:
                probs = validate_request("CreateEvaluator", p)
                bad += len(probs)
                print(f"  {p['evaluatorName']:30} {'ok' if not probs else '; '.join(probs)}")
            print(f"\n{bad} problem(s)")
            return 1 if bad else 0
        lock = read_lock()
        if not a.apply:
            for p in ps:
                print(f"  create    {p['evaluatorName']}")
            print("\nDRY RUN — nothing was sent. re-run with --apply")
            return 0
        import boto3
        from .naming import to_judge_id
        c = boto3.client("bedrock-agentcore-control", region_name=a.region)
        for p in ps:
            res = c.create_evaluator(**p)
            eid = res.get("evaluatorId") or res.get("id")
            lock["judges"][to_judge_id(p["evaluatorName"])] = {
                "evaluatorId": eid, "evaluatorName": p["evaluatorName"], "level": p["level"]}
            print(f"  created   {p['evaluatorName']}  {eid}")
        write_lock(lock)
        return 0

    if a.cmd == "score":
        # dict.fromkeys, not set: a repeated --judge must not be billed twice in
        # the count the operator confirms, and the report rows must stay in the
        # order they were asked for.
        judges = list(dict.fromkeys(a.judges or default_judges()))
        prompts = plan_prompts(judges, a.limit, a.seed, a.k)
        shortfalls = band_shortfalls(judges, a.limit)
        if a.dry_run or not api_key_present():
            print(dry_run_report(prompts, a.model, a.effort, a.k, shortfalls))
            return 0
        # Before confirm(), and so before a single call: an unwritable --out
        # discovered after the sweep costs the whole sweep and saves nothing.
        out = prepare_out(a.out) if a.out else None
        items = len({(p.judge_id, p.item_id) for p in prompts})
        if refusal := confirm(prompts, len(judges), items, a.k, a.yes):
            print(refusal, file=sys.stderr)
            return 2
        results = run_live(prompts, a.model, a.effort)
        print(render_report([metrics_for(j, [r for r in results if r.judge_id == j])
                             for j in judges], shortfalls))
        if out:
            write_results(out, results)
            print(f"\n{len(results)} items written to {out}")
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
            print(json.dumps(payload, indent=2)); return 0
        import boto3
        c = boto3.client("bedrock-agentcore-control", region_name=a.region)
        print("created:", c.create_online_evaluation_config(**payload)
              .get("onlineEvaluationConfigId"))
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
