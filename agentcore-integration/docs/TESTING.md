# Testing strategy — AgentCore, code-based Lambda

This path preserves almost everything, so almost everything is testable. The exceptions are timing and the span seam.

> **Baseline: no judge on this path has scored a real item.** Everything below is
> the plan, not results.

This folder is standalone, so this file repeats the shared method. What differs by
path is in *What this path makes hard/easy to test* below — read that first if you
have already read a sibling.

## What this path makes easy

Because the Lambda calls Anthropic itself, **k=5, the median, the spread and the
schema all survive** — so questions #2 and #4 are answerable directly, with the
same instruments the parent harness uses. The payload is schema-validated at
generation time, so `verify` is checking serialisation rather than shape.

## What this path adds that must be tested

**Two things no other path has**, and both can fail silently:

| Test | Why | How |
|---|---|---|
| **Lambda timing** | k=5 concurrent Opus calls at `effort: high` must finish inside 300 s. This is the top sizing risk and the only limit that can force a redesign. | Time a real transcript before provisioning. If it does not fit: k=3 in production, k=5/k=10 offline. |
| **Span reconstruction** | `spans.py` rebuilds the transcript from CloudWatch. If it drifts from the parent renderer, every rubric's meaning drifts and no test would say so. | The suite asserts byte-identity against `harness/transcript.py`. Keep that test. |

**Tool attribution** deserves its own check. Tool spans must attach to the
assistant turn of *their own trace* — attaching them to the most recent turn
reconciles a turn's claims against a different turn's tool calls, which is exactly
the failure `capability-honesty-traced` exists to detect. A test pins it; do not
delete it.

## Path-specific perturbations

| Perturbation | Requirement |
|---|---|
| Mark a tool span ERROR | `capability-honesty-traced` C5 must fail if the turn does not disclose it |
| Remove `session.id` | SESSION judges must produce nothing rather than scoring a fragment |
| Exceed 6 MB `sessionSpans` | Must fail loudly, not silently truncate to a partial conversation |


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
.venv/bin/python -m tests.test_suite     # 51 offline checks, no credentials
bin/agentcore-judges validate --lambda-arn ARN
bin/agentcore-judges check-drift
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
