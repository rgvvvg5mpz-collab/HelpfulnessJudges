# Labelled test sets

660 samples: **60 per judge — 20 pass (1.0), 20 warning (0.5), 20 hard failure (0)**,
across 10 judges plus the traced variant.

```
data/testsets/<judge-id>.jsonl
```

## What each row contains

| Field | |
|---|---|
| `id` | unique, prefixed with the judge id |
| `judge_id` | the judge this sample is for |
| `expected_score` | `1.0`, `0.5` or `0` |
| `scenario` | short situation label |
| `turns` | `[{role, text, tool_trace}]`, opening with a customer turn |
| `target_turn` | index of the assistant turn under review |
| `trigger_present` | whether the dimension's trigger condition appears |
| `rationale` | which checks pass and fail; for a `0`, **which named bright line fires** |
| `discriminating_property` | why the sample is diagnostic for *this* judge and not the others |
| `contested` | present only where the verifier disagreed — see below |

## How they were built

One generator per judge, given that judge's full rubric and the shared preamble,
then an **independent adversarial verifier** given the same rubric and asked to
score all 60 blind and flag disagreements. Verifier agreement ran 52–60 of 60.

Two constraints were imposed on every set, because an ungated suite fails
silently without them:

- **At least 6 pass samples with `trigger_present: false`** — the "nothing to get
  wrong here is a pass" case that a gated judge never had to handle.
- **At least 4 warning samples that are the *imposition* failure** — the assistant
  manufacturing the dimension where it did not belong.

Conversation-unit judges (`conduct-adaptation`,
`effort-and-resolution-path`) have multi-turn conversations in all 60 samples; a
two-turn exchange cannot exercise them.

## `contested` — 68 of 660 samples

Where the verifier's independent score differed from the generator's, or it judged
a sample non-diagnostic, the row carries:

```json
"contested": {"problem": "wrong_label", "verifier_score": 1.0, "note": "..."}
```

**The generator's label is kept as `expected_score`.** Neither model is ground
truth, so overwriting one with the other would hide the disagreement rather than
resolve it — and preserving both keeps the 20/20/20 balance intact. These 68 rows
are the items an SME should adjudicate first: two capable models reading the same
rubric reached different conclusions, which usually means the *rubric* is
ambiguous at that point, not that one model erred.

## What these are not

**These are not the gold set.** The gold set in
[../../docs/validation-protocol.md](../../docs/validation-protocol.md) must be
human-labelled by SMEs, drawn from real traffic, and split into
development / validation / frozen-anchor partitions. These samples are
model-written and model-labelled: they inherit whatever the generating model got
wrong, and validating a judge against them measures agreement with a model that
read the same rubric — which is circular.

Use them for what they are good for:

- **Rubric debugging.** Run a judge over its set and read the disagreements. A
  cluster of them usually points at an ambiguous rubric clause.
- **Regression testing.** Freeze a run, then re-run after any rubric edit. A comma
  in a rubric is a code change, and this is the cheapest way to see what it moved.
- **Cross-judge isolation.** Run *every* judge over *one* judge's set. Only the
  owning judge should vary much; if three judges move together on the same
  samples, they are not measuring distinct constructs.
- **Calibration exemplars.** The clean, uncontested samples are candidates for
  few-shot anchors — though anchors must be A/B tested against human labels
  before adoption, not assumed to help.

## Using them

```bash
bin/agentcore-judges score --dry-run --limit 6                      # free: builds every prompt
bin/agentcore-judges score --judge actionability --limit 6 \
    --out runs/actionability.jsonl --yes
bin/agentcore-judges verify runs/actionability.jsonl
```

The shape and balance checks are in `tests/test_suite.py`, which reads every set
on every run. `score` draws its samples balanced across the three bands and at a
fixed seed — an unbalanced draw moves the exact-match rate on a 20/20/20 set
without any judge behaviour changing.

`score` reports exact-match rate and, separately, the hard-failure confusion —
how often the judge cries failure where the label is not 0 (`fp0`) and how often
it misses one (`fn0`). The second pair is what matters: a 1.0-vs-0.5 disagreement
is about polish, a 0.5-vs-0 disagreement is about whether something was broken.
