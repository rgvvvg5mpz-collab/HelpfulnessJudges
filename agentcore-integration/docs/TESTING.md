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
.venv/bin/python -m tests.test_suite     # 134 offline checks, no credentials
bin/agentcore-judges validate --lambda-arn ARN
bin/agentcore-judges check-drift
bin/agentcore-judges score --dry-run     # every prompt built, nothing sent
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

## Scoring rubrics offline

`bin/agentcore-judges score` runs a judge over its own `data/testsets/` set and
prints the result table. Until it existed the folder shipped the strategy above
and the 660 samples to execute it with, and nothing that joined the two: the only
way to find out whether a rubric edit helped was to provision evaluators against a
live account and read CloudWatch afterwards.

```bash
bin/agentcore-judges score --dry-run --limit 6            # free, no key, no network
bin/agentcore-judges score --judge signal-density --limit 6 --out runs/sd.jsonl --yes
bin/agentcore-judges score --limit 0 --k 5 --yes          # the full 600-item baseline
```

| Flag | |
|---|---|
| `--judge` | repeatable; default is the deployable ten, so a run cannot score `capability-honesty` and its traced variant at once and double-count the defect |
| `--limit` | samples per judge, **balanced across the three bands** — `6` is 2 pass / 2 warning / 2 fail. `0` is all 60. An uneven limit gives the remainder to the lowest band first |
| `--k` | samples per item, default 5, which is what the deployed Lambda runs |
| `--seed` | the draw is seeded, so two runs of `--limit 6` compare |
| `--dry-run` | builds every prompt and calls nothing |
| `--out` | per-item JSONL, in the shape `agentcore-judges verify` already reads. Resolved, created and proved writable **before** the cost confirmation, so a bad path costs a re-run rather than a run |

Balance is not cosmetic. The sets are 20/20/20 by construction, so an unbalanced
draw moves the exact-match rate with no judge behaviour changing at all.

### It scores this folder's artifact, not the rubric

Every item that produces a verdict goes through the path a real invocation takes —
`spans.Conversation.render` for the transcript, `judge.build_request` for the prompt,
`boundary.agentcore_result` for the return boundary, `verify` for the payload. That
is deliberate. The rubric text is shared with two sibling folders and is the *least*
likely thing to be wrong; the render and the compaction exist only here.

`boundary.agentcore_result` is the function `handler` itself returns — imported, not
reimplemented — so "the scorer compacts exactly as the Lambda does" is a fact about
the import graph rather than two copies that have to be kept in step. The suite
asserts the identity (`handler._compact is score._compact`). An item that produces
NO verdict reaches no boundary at all, because there is nothing to compact; see
`transport`, below.

The report is identical in shape across the three integration folders, because a
difference in the numbers is only evidence about a platform if the two runs were
counted the same way:

```
judge                            n   exact  ±1 lvl   fp0   fn0   pass  warn  fail
------------------------------------------------------------------------------
actionability                    6   83.3%  100.0%     0     0   2/2   1/2   2/2
unparseable                      0
transport                        0   (excluded from the table — no answer received)

fp0 = judge scored 0 where the label was not 0 (false alarm)
fn0 = label was 0 and the judge did not catch it (missed bright line)
```

The two lines under the table mean opposite things, and keeping them apart is the
whole point of having both.

`unparseable` is an item whose output did not survive this folder's own return
boundary — no JSON, no score field, a score off the three-point scale, a payload
lost in compaction. Those items stay in `n` and in their band's denominator —
dropping them would let the failure this scorer exists to detect improve the number
it reports — and an unparseable item on a `0` label is also counted as `fn0`,
because the bright line went uncaught and the reason it went uncaught does not
change that.

`transport` is an item where **no answer was ever received**: a connection reset, a
429, an expired key, a timeout. That is a fact about the network, not about the
judge, so it is excluded from `n`, `exact`, `±1 lvl`, `fp0`, `fn0` and the band
columns, and printed on its own line so a shrunken `n` has a stated cause instead of
being a silent hole in the denominator. Counting one as `fn0` would report an outage
as "this judge missed a hard failure", which is the most misleading thing a
measurement tool can say. If a whole run is transport failures the report says
`NOTHING WAS MEASURED` rather than printing empty rates that read like a clean sweep.
An item is only transport if **every** sample failed that way; one answer that came
back and broke the contract makes the item unparseable, because that much is
measurable.

If a band holds fewer items than `--limit` asks for, the draw takes what exists —
and says so, under both the dry run and the report, so nobody reads a 4-item column
as a 6-item one.

Below the legend the run lists any item where the model's stated score disagrees
with the label recomputed from its own checks (`verify.recompute_label`). That is a
finding about the rubric or the model, not a bug in the scorer: a payload that
passes every check and scores 0.5 means the rubric's mapping rule is not being read
the way it is written.

### Cost safety

A full sweep is 10 judges x 60 samples x k=5 = 3,000 Opus calls at `effort: high`.
So: `--dry-run` is the **default** whenever no API key is in the environment and
exits 0 rather than raising an auth error; the exact call count is printed before
any live run; and a non-interactive shell without `--yes` refuses. Nothing here
defaults to the full 600.

The cost gate has a cost of its own: everything past the confirmation is
unreachable in a dry run, so no free command ever executes it. The offline suite
therefore drives the whole paid path in-process with a stub standing in for the
`anthropic` module — client construction, the per-item loop, the table, `--out`.
It is the only coverage that half of the verb gets, and it caught an unbound name
that would have raised only after an operator had already approved the spend.

### Which of the questions above this answers

- **#2, self-consistency** — directly. `--k` fires k samples per item and `--out`
  records every sample, the median, and `needs_human_review`, so the spread is in
  the file rather than lost behind the label.
- **Regression after a rubric edit** — this is the instrument. Freeze a `--limit 0`
  run, edit the rubric, re-run at the same `--seed`, diff.
- **Phase 0's two free tasks** — the 22 disputed trigger clauses and the 10 genuine
  score disagreements. Score the affected judges, read the divergence lines.
- **Cross-judge isolation** — `--judge` is repeatable, so several judges can be run
  over one judge's set once the testset path is pointed by hand.

What it does **not** answer, and must not be reported as if it did:

- **#1, validity.** The testsets are model-written and model-labelled. Agreement
  with them is agreement with a model that read the same rubric. Still circular.
- **The Lambda timing risk.** The scorer runs in a shell with no 300 s ceiling. It
  says nothing about whether k=5 concurrent Opus calls fit inside the Lambda.
- **The span seam.** Items are built from testset turns, so the scorer exercises
  `Conversation.render` but never `from_session_spans`. The CloudWatch half of
  `spans.py` is still covered only by the offline suite's span fixtures.

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

1. **Phase 0** — free. Offline suite, the validate/drift commands above,
   `score --dry-run`, the 22 trigger clauses, the 10 score splits.
2. **Cheap first run** — `score --limit 6` across the suite, then the 68 flagged
   items. Confirms the clause fixes and gives the first real numbers.
3. **Regression baseline** — `score --limit 0`, frozen, so every later change diffs
   against it.
4. **Cross-path agreement** — the same items through the other two folders. All
   three run rubrics derived from one source, so disagreement isolates the
   platform from the judge.
5. **Phase 3** — the SME ceiling, then the gold set. Everything above is
   reliability; this is the first validity evidence.
6. **Phase 4** — outcome correlation.
