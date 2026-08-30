# Testing strategy — Arize AX

What can and cannot be tested when Arize executes the judges.

> **Baseline: no judge on this path has scored a real item.** Everything below is
> the plan, not results.

This folder is standalone, so this file repeats the shared method. What differs by
path is in *What this path makes hard/easy to test* below — read that first if you
have already read a sibling.

## What this path makes hard to test

**No k.** Arize has no repetition primitive — duplicates are rejected and
re-running replaces rather than appends. So question #2 (self-consistency) cannot
be answered in-platform at all. Run it offline against the parent harness and
treat the Arize number as a single draw.

**No schema.** The payload in `explanation` is a model-authored string with
nothing validating it. `arize_judges.verify` reconstructs the schema on the export
path, which turns the gap into two metrics you would not otherwise have:

| Metric | Why it matters |
|---|---|
| **payload validity rate** | A judge degrades here *before* its scores move |
| **label divergence rate** | The model's label vs what its own `bl` + `chk` imply |

Alarm on both. A judge whose payloads stop parsing is failing silently.

**No in-platform check queries.** Arize filters are equality and comparison only,
so "every span where C4 failed" is unanswerable there. `arize-judges export`
lands a per-check table in your own store; that is where questions 3 and 4 get
answered.

## Path-specific perturbations

Beyond the shared gates, two that only matter here:

| Perturbation | Requirement |
|---|---|
| Re-render the template in the other brace style | Data Preview must still substitute the variable — a half-converted template does not error, it scores a literal placeholder |
| Grow the payload past 10,000 chars | Must be caught by `verify`, not silently truncated by the platform |


## The five questions

They get conflated. They need different instruments, and only one tests *validity*.

| # | Question | Instrument | Needs people? |
|---|---|---|---|
| 1 | Does it agree with people? | Ordinal-weighted Krippendorff's α; the alt-test | **Yes** |
| 2 | Is it self-consistent? | Repeat the same input | No |
| 3 | Is it measuring what it claims? | Factor analysis over check vectors | No |
| 4 | Is it gameable? | Perturbation gates | No |
| 5 | Does it predict anything real? | Correlation with repeat-contact, escalation, CSAT | Production data |

Only #1 tests validity. A judge can be perfectly self-consistent, perfectly
distinct, perfectly robust — and perfectly wrong.

## Phase 0 — free, and it comes first

```bash
.venv/bin/python -m tests.test_suite     # 49 offline checks, no credentials
bin/arize-judges validate-suite 2>/dev/null || bin/arize-judges check-drift
```

Then the two things that cost nothing and are upstream of every measurement:

**Fix the trigger clauses.** When the 660 labelled samples in `data/testsets/`
were generated, an independent verifier read the same rubric and objected where
it disagreed. **22 of 68 objections were about `trigger_present`**, not about
scores — concentrated in `calibrated-hedging` (7), `capability-honesty-traced`
(4) and `signal-density` (4). Two capable models read those disjunctive trigger
clauses differently, so the clauses are the defect.

**Read the 10 genuine score disagreements.** Distinct from the above: 10 items
where the two models gave *different* scores. Each marks a band boundary that is
not sharp enough.

> The `contested` field in `data/testsets/` is misnamed — 68 rows carry it but
> only 10 are score disagreements. `verdict_counts.disagreed` is unreliable and
> should be ignored. Both are documented in `data/testsets/README.md`.

## Phase 3 — needs people, cannot be shortcut

**Measure SME-vs-SME agreement before measuring any judge.** Human experts reach
weighted κ ≈ 0.29 on the closest published empathy analogue. If two of your SMEs
agree at 0.62 on attunement, no judge can be held to 0.80 — and a validation memo
that omits the ceiling claims more than it measured.

Use **expert** labels, not crowd labels: agreement runs consistently higher
against non-experts, so validating against contractors and deploying against a
standard set by licensed representatives overstates what you measured.

## Phase 4 — the test that actually matters

Do the scores predict repeat-contact rate, escalation, CSAT, task completion?
Entirely separate from agreement, and the only evidence that acting on these
numbers improves anything. Needs production traffic, so it cannot be first — but
it should be scheduled, not left implicit.

## What the shipped test data can and cannot do

`data/testsets/` is **model-written and model-labelled**. Measuring agreement
against it is circular. It is good for **regression testing** after a rubric
edit, **self-consistency**, and **cross-judge isolation** — running every judge
over one judge's set, where only the owning judge should move. It is not a gold
set and must never be reported as one. The 20/20/20 split is enforced by
construction, so no rate computed on it estimates production.

## Recommended order

1. **Phase 0** — free. Offline suite, the transform/validate commands above, the
   22 trigger clauses, the 10 score splits.
2. **Cheap first run** — the 68 flagged items. Confirms the clause fixes and gives
   the first real numbers.
3. **Regression baseline** — the full 660, frozen, so every later change diffs
   against it.
4. **Cross-path agreement** — the same items through the other two folders. All
   three run rubrics derived from one source, so disagreement isolates the
   platform from the judge.
5. **Phase 3** — the SME ceiling, then the gold set. Everything above is
   reliability; this is the first validity evidence.
6. **Phase 4** — outcome correlation.
