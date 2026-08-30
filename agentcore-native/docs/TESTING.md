# Testing strategy — AgentCore native llmAsAJudge

This path rewrites the rubrics, which adds a whole category of testing the other two do not need.

> **Baseline: no judge on this path has scored a real item.** Everything below is
> the plan, not results.

This folder is standalone, so this file repeats the shared method. What differs by
path is in *What this path makes hard/easy to test* below — read that first if you
have already read a sibling.

## The test category unique to this path

**The transform is a new failure surface.** `transform.py` rewrites every rubric
before deployment, so a defect there changes what the judges measure without
touching a rubric file. That is why the test suite here is the largest of the
three (77 checks): five assertions per judge on the transform alone.

| Test | Catches |
|---|---|
| No transcript markers survive | A rubric still referring to `>>> TARGET`, which does not exist here |
| The right placeholder is present | A TRACE judge without `{assistant_turn}`, or a SESSION judge given one |
| Output-contract sections stripped | The judge following two contradictory output contracts |
| Rating labels match the scale exactly | The model asked for two different vocabularies |
| Check ids survive into the reasoning contract | A silently truncated check vector |

Run `native-judges diff` and read it. Everything the transform removes is
recorded — the drift being *visible* is the main safety property of this folder.

## What this path cannot test at all

**k=1 only.** No repetition parameter exists. Self-consistency is unanswerable
here; run it offline against the parent harness.

**Nothing enforces the verdict shape.** The `CHK C1=y …` flag line is a prompt
convention, not a schema. Parse it on export and alarm on the parse-failure rate
— that is the only detector.

**Evidence spans do not exist**, so the evidence-span verifier that the other two
paths run has nothing to check.

## The measurement that decides whether this path is acceptable

**Run the anchor set both ways** — this path and `../agentcore-integration` — and
measure the gap.

The prompts here are not the prompts that were validated, so the anchor-set
numbers do not transfer. That gap *is* the price of the simplicity, and it should
be a number in a memo rather than an assumption. If it is small, this path is a
bargain. If it is large, it is a different instrument wearing the same name.

## Path-specific perturbations

| Perturbation | Requirement |
|---|---|
| Inspect one rendered prompt in Test Evaluator | `{context}` must actually contain the conversation — its shape is undocumented |
| Same, for a judge needing tool calls | If `{context}` omits tool calls, the honesty judges lose their subject matter entirely |
| Re-run an identical item | Expect variance; there is no k to smooth it |


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
.venv/bin/python -m tests.test_suite     # 77 offline checks, no credentials
bin/native-judges diff        # exactly what the transform removes, per judge
bin/native-judges validate
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
