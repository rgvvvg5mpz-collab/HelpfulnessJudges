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
.venv/bin/python -m tests.test_suite     # 153 offline checks, no credentials
bin/arize-judges validate-suite 2>/dev/null || bin/arize-judges check-drift
bin/arize-judges score --dry-run --limit 6
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

`data/testsets/` shipped with nothing reading it. `arize-judges score` closes
that: it draws a band-balanced sample, fills each judge's **compiled AX template**
with a transcript from this folder's own `instrument.render_transcript`, calls
Anthropic directly, and reports against the labels.

```bash
bin/arize-judges score --dry-run --limit 6            # every prompt, no calls
bin/arize-judges score --judge signal-density --limit 6 --yes
bin/arize-judges score --limit 0 --out runs/base.jsonl --yes   # all 600, 10 judges x 60
```

| Flag | |
|---|---|
| `--limit N` | samples per judge, balanced across the three bands; remainder to the **lowest** band first. `0` = all 60 |
| `--k N` | draws per sample, default 1 |
| `--seed N` | deterministic sampling, default 0. The RNG is keyed per (seed, judge, band), so raising `--limit` **extends** each band's draw instead of reshuffling it, and the key matches `agentcore-integration` over the same testset files — one `--seed`/`--limit` draws the same items on both paths, which is what makes step 4 below a comparison |
| `--dry-run` | build every prompt and call nothing. **The default whenever no API key is set** |
| `--yes` | skip the cost confirmation. Required non-interactively |

A full sweep is 10 judges x 60 x k = 600 calls, so nothing runs without an
explicit call count and a confirmation. The default is 6 per judge — 60 calls,
never 600. (The 660 samples in `data/testsets/` cover 11 rubrics; only 10 deploy,
because the two `capability-honesty` variants are mutually exclusive.)

`--out` is resolved, its parent created and the file itself proved writable
**before** the confirmation, not after the calls. An unwritable path used to cost
a full sweep and then discard every result it had paid for.

`--dry-run` exits 0 — nothing was asked for. An explicit `--yes` with no
`ANTHROPIC_API_KEY` (or `ANTHROPIC_AUTH_TOKEN`) exits **2** and names the
variable: `--yes` is an operator asking to spend, and a silent dry run that
exits 0 is how a scheduled job reports "scored" for weeks having scored nothing.

**What it answers from the list above.** Question 2 (self-consistency) is
unanswerable in-platform on this path — `--k` answers it *for the rubric*, which
is the honest half of it and is exactly the split LIMITS.md prescribes. The
regression baseline in step 3 of the recommended order no longer needs a tenant:
freeze `--limit 0 --seed 0 --out`, edit a rubric, re-run, diff. And **payload
validity rate**, previously computable only on the export path from production
spans, is now measurable before the evaluator exists.

**The report.** The table is pinned character-for-character to the one the other
two deployment folders print, so the same items scored through each isolate the
platform from the judge — a column widened here would turn a cross-path diff into
a formatting diff:

```
judge                            n   exact  ±1 lvl   fp0   fn0   pass  warn  fail
------------------------------------------------------------------------------
actionability                    6   50.0%  100.0%     1     1   1/2   1/2   1/2
unparseable                      0
transport                        0   (excluded from the table — no answer received)
```

`exact` is score == label; `±1 lvl` is within half a level. `fp0` / `fn0` are the
hard-failure confusion and are what matters — a 1.0-vs-0.5 disagreement is about
polish, a 0.5-vs-0 disagreement is about whether something was broken.

**Two ways a draw can fail, and they are not the same failure.** The distinction
is the difference between measuring the judge and measuring the network:

| | What happened | Where it lands |
|---|---|---|
| **unparseable** | An answer arrived and broke the output contract — no tool call, a label outside `classification_choices`, a payload that lost its verdict | Stays in `n` and in the band denominators, and counts `fn0` when the label was 0. No 0 reached the platform, however the output failed |
| **transport** | No answer arrived — connection error, timeout, rate limit, rejected key | Excluded from `n`, `exact`, `±1 lvl`, `fp0`, `fn0` and every band column. Reported on its own line instead |

Folding the second into the first is how an outage gets printed as "this judge
missed a hard failure", which is the single most misleading thing this table
could say. Both rows print even at zero, so an exclusion is never invisible — and
a run that was *entirely* transport says `NOTHING WAS MEASURED` above the legend
rather than showing an empty table that reads as a clean sweep.

**Short bands.** `--limit` splits across three bands, and a band with fewer items
than its quota under-draws — correctly, there is nothing else to draw. That
shortfall is printed by the dry run and again under the report, per judge and per
band, because a `1/1` fail column otherwise reads as the `2/2` the flag implied.
The shipped sets are 20/20/20, so this only fires on an edited or truncated
testset — which is exactly when nobody is expecting it.

**An empty testset is an error.** A judge whose file exists but holds no rows used
to vanish from the report, and a missing row reads as "not selected" rather than
"this folder is broken". `load_testset` now refuses it by name.

Below the table, and only in this folder, is **payload well-formedness**. Arize
constrains the label to `classification_choices` and constrains the explanation to
nothing, so a judge can score correctly while its structured verdict silently
degrades to prose. It is `verify.verify` run over each explanation — the same code
the export path runs on production spans, run here before anything is provisioned.
It sits outside the shared table because the other two paths get a schema and have
no counterpart to it.

**What it does not test.** It does not test Arize. Sampling, cadence, write-back,
column mapping and the adapter's own defaults are untouched; only the template
string is shared with production, and that sharing is the point. Two deviations
both flatter the rubric: the direct path requests adaptive thinking at effort
`high`, which **cannot be requested through AX's evaluator schema at all** (see
LIMITS.md), and it sizes `max_tokens` for that thinking. Treat an offline score as
an upper bound on the deployed one. And the labels remain model-written — read
"What the shipped test data can and cannot do" below before quoting any number as
agreement.

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
   the first real numbers. `arize-judges score` runs this without a tenant.
3. **Regression baseline** — all 600 deployable items, frozen, so every later
   change diffs against it (`score --limit 0 --seed 0 --out`).
4. **Cross-path agreement** — the same items through the other two folders. All
   three run rubrics derived from one source, so disagreement isolates the
   platform from the judge.
5. **Phase 3** — the SME ceiling, then the gold set. Everything above is
   reliability; this is the first validity evidence.
6. **Phase 4** — outcome correlation.
