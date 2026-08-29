"""Labelled test sets: validate their shape, and measure a judge against them.

A test set is `data/testsets/<judge-id>.jsonl` — one JSON object per line, each a
conversation with an expected level. These are the sets you run a judge against
to get its agreement figure; they are NOT the gold set from
docs/validation-protocol.md, which must be human-labelled by SMEs. These labels
came from a model and inherit whatever that model got wrong.

    python -m harness.testsets validate
    python -m harness.testsets score runs/testset.aggregated.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from harness.judge_spec import JUDGE_DIR, load_all_judge_files

TESTSET_DIR = Path(__file__).resolve().parent.parent / "data" / "testsets"
LEVELS = (0.0, 0.5, 1.0)
REQUIRED = {
    "id",
    "judge_id",
    "expected_score",
    "scenario",
    "turns",
    "target_turn",
    "trigger_present",
    "rationale",
    "discriminating_property",
}


def load_testset(judge_id: str) -> list[dict[str, Any]]:
    path = TESTSET_DIR / f"{judge_id}.jsonl"
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def validate() -> int:
    errors: list[str] = []
    warnings: list[str] = []
    specs = {s.id: s for s in load_all_judge_files()}

    print(
        f"{'judge':30} {'n':>4} {'1.0':>5} {'0.5':>5} {'0':>4} {'trig':>5} "
        f"{'multi':>6} {'contest':>8}"
    )
    print("-" * 78)

    for judge_id, spec in sorted(specs.items()):
        rows = load_testset(judge_id)
        if not rows:
            errors.append(f"{judge_id}: no test set at data/testsets/{judge_id}.jsonl")
            continue

        dist = Counter(r.get("expected_score") for r in rows)
        trig = sum(1 for r in rows if r.get("trigger_present"))
        multi = sum(1 for r in rows if len([t for t in r.get("turns", []) if t["role"] == "assistant"]) > 1)
        contested = sum(1 for r in rows if r.get("contested"))
        print(
            f"{judge_id:30} {len(rows):>4} {dist[1.0]:>5} {dist[0.5]:>5} {dist[0.0]:>4} "
            f"{trig:>5} {multi:>6} {contested:>8}"
        )

        ids = Counter(r.get("id") for r in rows)
        if dupes := [i for i, n in ids.items() if n > 1]:
            errors.append(f"{judge_id}: duplicate sample ids {dupes[:3]}")

        for r in rows:
            missing = REQUIRED - r.keys()
            if missing:
                errors.append(f"{judge_id}/{r.get('id')}: missing {sorted(missing)}")
                continue
            if r["expected_score"] not in LEVELS:
                errors.append(f"{judge_id}/{r['id']}: score {r['expected_score']} not in {LEVELS}")
            if r["judge_id"] != judge_id:
                errors.append(f"{judge_id}/{r['id']}: judge_id mismatch ({r['judge_id']})")
            turns = r["turns"]
            if not turns or turns[0]["role"] != "customer":
                errors.append(f"{judge_id}/{r['id']}: must open with a customer turn")
            if not any(t["role"] == "assistant" for t in turns):
                errors.append(f"{judge_id}/{r['id']}: no assistant turn")
            tt = r["target_turn"]
            if not (0 <= tt < len(turns)) or turns[tt]["role"] != "assistant":
                errors.append(f"{judge_id}/{r['id']}: target_turn {tt} is not an assistant turn")
            c = r.get("contested")
            if c is not None and not {"problem", "verifier_score", "note"} <= c.keys():
                errors.append(f"{judge_id}/{r['id']}: malformed `contested` block")

        # A judge is only ungated-correct if its set exercises the no-trigger pass.
        no_trigger_pass = sum(
            1 for r in rows if r.get("expected_score") == 1.0 and not r.get("trigger_present")
        )
        if no_trigger_pass < 3:
            warnings.append(
                f"{judge_id}: only {no_trigger_pass} no-trigger passes — the ungated "
                f"'nothing to get wrong is a pass' case is barely covered"
            )
        no_trigger_warn = sum(
            1 for r in rows if r.get("expected_score") == 0.5 and not r.get("trigger_present")
        )
        if no_trigger_warn < 2 and judge_id != "signal-density":
            warnings.append(
                f"{judge_id}: only {no_trigger_warn} no-trigger warnings — the "
                f"imposition failure (manufacturing the dimension) is barely covered"
            )
        if spec.unit == "conversation" and multi < len(rows) * 0.8:
            warnings.append(
                f"{judge_id}: conversation-unit judge but only {multi}/{len(rows)} samples "
                f"have more than one assistant turn — most cannot exercise it"
            )

    total_contested = sum(
        1 for jid in specs for r in load_testset(jid) if r.get("contested")
    )
    print(
        f"\ncontest = an independent verifier scored the sample against the same rubric "
        f"and disagreed\n          with the generator, or judged the sample non-diagnostic. "
        f"{total_contested} of\n          {sum(len(load_testset(s)) for s in specs)} samples "
        f"carry one. These are the items to adjudicate first."
    )

    print()
    for w in warnings:
        print(f"WARN  {w}")
    for e in errors[:40]:
        print(f"ERROR {e}")
    if len(errors) > 40:
        print(f"... and {len(errors) - 40} more errors")
    if not errors:
        print("OK — all test sets well-formed" + (f" ({len(warnings)} warnings)" if warnings else ""))
    return 1 if errors else 0


def score(aggregated_path: str) -> int:
    """Compare an aggregated judge run against the expected labels.

    Reports exact-match rate and, separately, the hard-failure confusion — how
    often the judge calls 0 when the label is not 0 and vice versa. That second
    number is the one that matters: 1.0-vs-0.5 is a disagreement about polish,
    0.5-vs-0 is a disagreement about whether something was broken.
    """
    rows = [json.loads(l) for l in Path(aggregated_path).read_text().splitlines() if l.strip()]
    labels: dict[tuple[str, str], float] = {}
    for spec in load_all_judge_files():
        for r in load_testset(spec.id):
            labels[(r["judge_id"], r["id"])] = r["expected_score"]

    by_judge: dict[str, list[tuple[float, float]]] = {}
    for row in rows:
        key = (row["judge_id"], row["conversation_id"])
        if key in labels:
            by_judge.setdefault(row["judge_id"], []).append((labels[key], row["score"]))

    if not by_judge:
        print("no scored items matched a test-set label", file=sys.stderr)
        return 1

    print(f"{'judge':30} {'n':>4} {'exact':>7} {'±1 lvl':>7} {'fp0':>5} {'fn0':>5}")
    print("-" * 64)
    for judge_id, pairs in sorted(by_judge.items()):
        n = len(pairs)
        exact = sum(1 for e, a in pairs if e == a)
        near = sum(1 for e, a in pairs if abs(e - a) <= 0.5)
        fp0 = sum(1 for e, a in pairs if a == 0 and e != 0)   # judge cried failure
        fn0 = sum(1 for e, a in pairs if e == 0 and a != 0)   # judge missed a failure
        print(
            f"{judge_id:30} {n:>4} {exact / n:>6.1%} {near / n:>6.1%} {fp0:>5} {fn0:>5}"
        )
    print("\nfp0 = judge scored 0 where the label was not 0 (false alarm)")
    print("fn0 = label was 0 and the judge did not catch it (missed bright line)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command", choices=["validate", "score"])
    ap.add_argument("path", nargs="?", help="aggregated run file, for `score`")
    args = ap.parse_args()
    if args.command == "validate":
        return validate()
    if not args.path:
        ap.error("score requires an aggregated run file")
    return score(args.path)


if __name__ == "__main__":
    raise SystemExit(main())
