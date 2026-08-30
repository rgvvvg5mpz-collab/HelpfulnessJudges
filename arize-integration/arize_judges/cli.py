"""arize-judges — compile, inspect and deploy the judge suite.

Offline (PyYAML only, no account needed):
    arize-judges list
    arize-judges check-drift
    arize-judges render --judge empathic-attunement [--out build/]
    arize-judges plan --space SPACE --integration INTEGRATION
    arize-judges verify payloads.jsonl

Talks to Arize (needs `pip install arize pandas` and ARIZE_API_KEY):
    arize-judges provision --space SPACE --integration INTEGRATION --apply
    arize-judges tasks --project PROJECT --apply
    arize-judges export --space SPACE --project PROJECT --hours 24
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .export import expand, summarise
from .naming import eval_columns
from .provision import LlmSettings, apply, plan, read_lock, task_plan
from .render import render_all
from .spec import check_drift, deployable, load_rubrics
from .verify import verify

TIERS = {
    # 100% so this cohort is a guaranteed superset of the sampled one — there is
    # no seed or stratification to align cohorts otherwise.
    "critical": {"sampling_rate": 1.0, "judges": [
        "capability-honesty", "need-coverage", "actionability", "calibrated-hedging"]},
    # The validation protocol already restricts the relational judges to
    # directional use, so sampling them is honest rather than a corner cut.
    "directional": {"sampling_rate": 0.1, "judges": [
        "empathic-attunement", "emotional-accuracy", "conduct-adaptation",
        "signal-density", "plain-language-clarity", "effort-and-resolution-path"]},
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="arize-judges", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="show the suite and what would deploy")
    sub.add_parser("check-drift", help="fail if the vendored rubrics differ from ../judges")

    r = sub.add_parser("render", help="compile rubrics to AX template strings")
    r.add_argument("--judge"); r.add_argument("--out", type=Path)
    r.add_argument("--brace-style", choices=["single", "double"], default="single")

    for name in ("plan", "provision"):
        p = sub.add_parser(name, help="compute (plan) or reconcile (provision) evaluators")
        p.add_argument("--space", required=True); p.add_argument("--integration", required=True)
        p.add_argument("--model", default="claude-opus-5")
        p.add_argument("--max-tokens", type=int, default=8000)
        p.add_argument("--temperature", type=float, default=0.0)
        p.add_argument("--brace-style", choices=["single", "double"], default="single")
        p.add_argument("--traced", action="store_true", help="use the trace-verified honesty variant")
        if name == "provision":
            p.add_argument("--apply", action="store_true", help="actually call Arize")

    t = sub.add_parser("tasks", help="build the online task payloads")
    t.add_argument("--project", required=True); t.add_argument("--apply", action="store_true")

    v = sub.add_parser("verify", help="validate payloads from a JSONL file")
    v.add_argument("path", type=Path)

    e = sub.add_parser("export", help="read evals back, validate, expand check vectors")
    e.add_argument("--space", required=True); e.add_argument("--project", required=True)
    e.add_argument("--hours", type=int, default=24); e.add_argument("--out", type=Path)

    a = ap.parse_args(argv)
    try:
        return _run(a)
    except (ValueError, FileNotFoundError) as e:
        # Operator errors (missing lockfile, unpinned version, unknown judge) get a
        # message, not a traceback. Real bugs still raise.
        print(f"error: {e}", file=sys.stderr)
        return 2


def _run(a) -> int:
    if a.cmd == "list":
        rs = load_rubrics()
        dep = {x.id for x in deployable(rs)}
        print(f"{'judge':30} {'unit':13} {'checks':>6} {'bright':>7}  deployed")
        print("-" * 72)
        for x in rs:
            print(f"{x.id:30} {x.unit:13} {len(x.check_ids):>6} {len(x.bright_lines):>7}"
                  f"  {'yes' if x.id in dep else 'no (variant)'}")
        print(f"\n{len(dep)} of {len(rs)} deploy. Variants are mutually exclusive — Arize has no "
              f"guard, so it is enforced here.")
        return 0

    if a.cmd == "check-drift":
        probs = check_drift()
        real = [p for p in probs if "changed" in p or "deleted" in p]
        if not probs:
            print("in sync with ../judges")
        elif not real:
            # No parent repo present. That is the normal standalone case, not a
            # failure — this folder carries everything it needs.
            print(f"no reference to compare against ({probs[0]})")
            print("this folder is standalone and still works; drift cannot be checked here")
        else:
            for p in real:
                print(f"DRIFT {p}")
            print(f"\n{len(real)} file(s) drifted")
        return 1 if real else 0

    if a.cmd == "render":
        rs = load_rubrics([a.judge]) if a.judge else deployable(load_rubrics())
        for t_ in render_all(rs, a.brace_style):
            if a.out:
                a.out.mkdir(parents=True, exist_ok=True)
                f = a.out / f"{t_.judge_id}.txt"
                f.write_text(t_.template)
                print(f"{t_.judge_id:30} {t_.chars:>6} chars  {t_.hash}  -> {f}")
            else:
                print(t_.template)
        return 0

    if a.cmd in ("plan", "provision"):
        llm = LlmSettings(ai_integration_id=a.integration, model_name=a.model,
                          max_tokens=a.max_tokens, temperature=a.temperature)
        if a.cmd == "plan":
            print(json.dumps(plan(a.space, llm, brace_style=a.brace_style,
                                  prefer_traced=a.traced), indent=2))
            return 0
        res = apply(a.space, llm, brace_style=a.brace_style,
                    prefer_traced=a.traced, dry_run=not a.apply)
        for act in res["actions"]:
            print(f"{act['action']:12} {act['judge_id']}")
        print(f"\n{'APPLIED' if a.apply else 'DRY RUN — nothing was sent'}  (git {res['git_sha']})")
        if not a.apply:
            print("re-run with --apply to create them")
        return 0

    if a.cmd == "tasks":
        payloads = task_plan(a.project, TIERS)
        if not a.apply:
            print(json.dumps(payloads, indent=2))
            print("\nDRY RUN. re-run with --apply", file=sys.stderr)
            return 0
        from arize import ArizeClient
        from arize._generated.api_client.models.task_evaluator_input import TaskEvaluatorInput
        from arize._generated.api_client.models.task_type import TaskType
        c = ArizeClient()
        for p in payloads:
            res = c.tasks.create_evaluation_task(
                name=p["name"], task_type=TaskType.TEMPLATE_EVALUATION, project=p["project"],
                evaluators=[TaskEvaluatorInput.from_dict(e) for e in p["evaluators"]],
                is_continuous=p["is_continuous"], sampling_rate=p["sampling_rate"],
                query_filter=p["query_filter"])
            print(f"created task {p['name']}: {getattr(res, 'id', '?')}")
        return 0

    if a.cmd == "verify":
        rubrics = {x.id: x for x in load_rubrics()}
        bad = 0
        for line in a.path.read_text().splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            rb = rubrics[rec["judge_id"]]
            res = verify(rb, rec.get("explanation"), rec.get("label"),
                         transcript=rec.get("transcript"))
            if not res.ok:
                bad += 1
                print(f"FAIL {rec['judge_id']}: {res.errors[0]}")
            for w in res.warnings:
                print(f"warn {rec['judge_id']}: {w}")
        print(f"\n{bad} invalid payload(s)")
        return 1 if bad else 0

    if a.cmd == "export":
        import datetime as dt
        from .export import fetch
        rs = deployable(load_rubrics())
        end = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=2)  # index lags 1-2h
        start = end - dt.timedelta(hours=a.hours)
        df = fetch(a.space, a.project, start, end, rs)
        rows, reports = expand(df.to_dict("records"), rs)
        print(summarise(reports))
        if a.out:
            a.out.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
            print(f"\n{len(rows)} check rows -> {a.out}")
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
