"""Static consistency checks over the judge suite.

Run before any rubric change ships: .venv/bin/python -m harness.validate_suite
"""
from __future__ import annotations

import json
import re
import sys

from harness.judge_spec import SCORING_TAIL, load_all_judge_files, load_judges, load_shared_preamble

REQUIRED_SECTIONS = ["## Score", "## Binary checks"]
ERRORS: list[str] = []
WARNINGS: list[str] = []


def main() -> int:
    specs = load_all_judge_files()
    ids = {s.id for s in specs}
    preamble = load_shared_preamble()
    tail = SCORING_TAIL.read_text(encoding="utf-8")

    variants = [s for s in specs if s.variant_of]
    print(f"{len(specs)} judge files ({len(specs) - len(variants)} in the default suite, "
          f"{len(variants)} variant)\n")
    print(f"{'judge':32} {'unit':13} {'tier':11} {'checks':>6} {'body':>6}")
    print("-" * 78)

    for s in specs:
        print(
            f"{s.id:32} {s.unit:13} {s.meta['tier']:11} "
            f"{len(s.meta.get('checks', [])):>6} {len(s.body.split()):>6}"
        )

        # contract
        n_checks = len(s.meta.get("checks", []))
        if not 5 <= n_checks <= 8:
            ERRORS.append(
                f"{s.id}: {n_checks} checks; the rubric literature puts the "
                f"usable range at 5-8 (complexity past that reduces agreement)"
            )
        cids = [c["id"] for c in s.meta.get("checks", [])]
        if len(set(cids)) != len(cids):
            ERRORS.append(f"{s.id}: duplicate check ids")

        # schema must be well-formed and self-consistent
        schema = s.output_schema
        json.dumps(schema)
        if schema["properties"]["checks"]["minItems"] != n_checks:
            ERRORS.append(f"{s.id}: schema checks cardinality != declared checks")
        if set(schema["required"]) - set(schema["properties"]):
            ERRORS.append(f"{s.id}: required names a property that does not exist")

        # every declared check must be explained in the body
        for cid in cids:
            if not re.search(rf"\*\*{cid} ", s.body):
                ERRORS.append(f"{s.id}: check {cid} declared but not described in the rubric body")

        # the body must define all three score levels
        for label in ("**1.0 — Pass.**", "**0.5 — Warning.**", "**0 — Hard failure.**"):
            if label not in s.body:
                ERRORS.append(f"{s.id}: no written description for level {label}")

        # ungated design: every rubric must say what a no-trigger item scores
        if "## Scoring when the trigger is absent" not in s.body:
            ERRORS.append(f"{s.id}: no 'Scoring when the trigger is absent' section — "
                          f"an ungated judge must define the no-trigger outcome")
        if "## The trigger" not in s.body:
            ERRORS.append(f"{s.id}: no 'The trigger' section")

        # bright lines must be enumerated, since they override the check count
        if "bright line" not in s.body.lower():
            ERRORS.append(f"{s.id}: hard-failure section names no bright lines")

        for section in REQUIRED_SECTIONS:
            if section not in s.body:
                ERRORS.append(f"{s.id}: missing section {section!r}")

        # trigger is a reporting key, not a gate — but must be declared
        if not s.meta.get("trigger"):
            ERRORS.append(f"{s.id}: no trigger declared")
        if "gating" in s.meta:
            ERRORS.append(f"{s.id}: 'gating' is gone from this design; use 'trigger'")
        if s.scale.get("type") != "levels" or s.scale.get("values") != [0, 0.5, 1.0]:
            ERRORS.append(f"{s.id}: scale must be levels [0, 0.5, 1.0]")

        # cross-references must resolve
        blob = " ".join(s.meta.get("de_confliction", [])) + " " + s.body
        for ref in set(re.findall(r"`([a-z][a-z-]+)`", blob)):
            if "-" in ref and ref not in ids and ref not in {
                "trigger-present", "confidence", "reasoning", "evidence", "checks",
                "score", "injection-suspected",
            }:
                WARNINGS.append(f"{s.id}: references `{ref}`, which is not a judge id")

        if not s.meta.get("grounding"):
            ERRORS.append(f"{s.id}: no grounding citations")

        # placeholders must be renderable
        if s.placeholders:
            ERRORS.append(f"{s.id}: rubric body has unresolved placeholders {s.placeholders}")

    # shared scaffolding
    if "{{scope}}" not in tail:
        ERRORS.append("_scoring-tail.md: lost the {{scope}} placeholder")
    if "0.5" not in preamble or "Hard failure" not in preamble:
        ERRORS.append("_preamble.md: no longer states the three-level scale")
    for marker in ("[BEGIN TRANSCRIPT]", "[END TRANSCRIPT]"):
        if marker not in preamble:
            ERRORS.append(f"_preamble.md: no longer names {marker}, which transcript.py emits")

    # suite-level composition
    units = {s.id: s.unit for s in specs}
    if not any(u == "conversation" for u in units.values()):
        ERRORS.append("suite: no conversation-level judge; doom loops would be unmeasurable")
    for s in variants:
        if s.variant_of not in {x.id for x in specs}:
            ERRORS.append(f"{s.id}: variant_of names {s.variant_of!r}, which does not exist")
    tiers = {}
    for s in specs:
        if s.variant_of:
            continue
        tiers.setdefault(s.meta["tier"], []).append(s.id)
    print("\ntiers: " + ", ".join(f"{k}={len(v)}" for k, v in sorted(tiers.items())))

    print()
    for w in WARNINGS:
        print(f"WARN  {w}")
    for e in ERRORS:
        print(f"ERROR {e}")
    if not ERRORS:
        print("OK — no errors" + (f" ({len(WARNINGS)} warnings)" if WARNINGS else ""))
    return 1 if ERRORS else 0


if __name__ == "__main__":
    raise SystemExit(main())
