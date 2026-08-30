# Helpfulness Judges

Ten independent prompt-based LLM judges for a retail-investment chatbot —
empathy, emotional intelligence, completeness, actionability, clarity, effort,
accommodation and honesty — plus one variant judge.

**Published site: <https://rgvvvg5mpz-collab.github.io/HelpfulnessJudges/>** — the
two readmes below are HTML, so on github.com they render as source. Read them there.

Two readmes, both HTML, both opened in a browser:

- **[README.interactive.html](README.interactive.html)** — filter the suite,
  toggle checks to watch a level resolve, quiz yourself against the 660 labelled
  samples, and browse the corpus. Self-contained; no server needed.
- **[README.html](README.html)** — the static reference. Same suite table and a
  worked pass / warning / hard-failure example per judge, in reading order.

This file is the index, and it is the one place the three deployment paths are
compared side by side. Everything else substantive lives in the documents below.

| | |
|---|---|
| [README.interactive.html](README.interactive.html) | Interactive readme — filters, live level simulator, calibration quiz, corpus browser. |
| [README.html](README.html) | Static readme. Suite table, per-judge descriptions, worked examples. |
| [docs/whitepaper.md](docs/whitepaper.md) | Construct-by-construct rationale, design decisions, full bibliography (77 refs). |
| [docs/literature-review.md](docs/literature-review.md) | The underlying review, with citation-confidence flags. |
| [docs/methodology.md](docs/methodology.md) | Every prompt-architecture decision and its source. |
| [docs/validation-protocol.md](docs/validation-protocol.md) | Gold set, agreement targets, drift monitoring, ship gates — the standard. |
| [docs/proposed-testing.md](docs/proposed-testing.md) | What to test first, what it costs, and what we already know without running anything — the plan. |
| [architecture.html](architecture.html) | The whole system on one page — source, reference runner, three paths, and what each gives up. |
| [judges/](judges/) | The prompts — the source of truth all three paths derive from. |
| [harness/](harness/) | The reference runner — the implementation all three paths were ported from and are validated against. Live and Batch execution, k-sample aggregation, the static validators. |
| [tools/check_vendoring.py](tools/check_vendoring.py) | Cross-folder drift check. The only thing that sees all four implementations, so the only thing that can catch an edited vendored copy or a missing folder. |
| [data/](data/) | Hand-written fixtures, plus [660 labelled test samples](data/testsets/README.md) — 60 per judge, 20 per level. |


## What this root is

Each of the three folders below does exactly one job and says so in its first
section. This root does five, and until now said so nowhere — which is what made
it look like a fourth, unfinished path. It is not. It is the upstream:

| | |
|---|---|
| **Source of truth** | `judges/` and `data/` are what all three deployment folders vendor from. Edit a rubric here, re-vendor, then run each folder's `check-drift`. |
| **Reference runner** | `harness/` is the implementation the three were ported from, and the only one that can batch-score a whole corpus or produce an agreement figure. It is the instrument, not a fourth product. |
| **The research** | `docs/` belongs to no single path — the whitepaper, the review, the methodology and the validation standard are about the judges themselves. |
| **The hub** | this file, where the three paths are compared. |
| **The published site** | `index.html` and the two HTML readmes. |

Nothing here is stale, and nothing here deploys. If you are shipping judges, go
to one of the three folders. If you are changing what a judge *is*, you are in
the right place.

## Three deployment paths

The same ten judges, three places to run them. All three share one set of source
rubrics in [judges/](judges/); each folder is **standalone** and carries its own
copy, docs, architecture diagram, test data and test suite.

| | Who runs the judge | Rubrics | k=5 | Schema | Reasoning effort | Choose when |
|---|---|---|---|---|---|---|
| **[agentcore-integration/](agentcore-integration/)**<br>AgentCore, code-based Lambda | your Lambda | **unchanged** | ✅ | ✅ enforced | ✅ | Fidelity matters. The numbers must be comparable to the validated anchor set. |
| **[arize-integration/](arize-integration/)**<br>Arize AX template evaluators | Arize | 1 file rewritten | ❌ | ❌ | ❌ **unreachable** | Arize is already the observability system of record. |
| **[agentcore-native/](agentcore-native/)**<br>AgentCore native `llmAsAJudge` | AgentCore | **rewritten** | ❌ | ❌ | ⚠️ unverified | You want judges running this week with zero infrastructure. |

**The short version.** `agentcore-integration` preserves the suite as validated,
because a Lambda calling Anthropic keeps the schema, the effort setting, prompt
caching and k=5. `arize-integration` gives up reasoning effort entirely — graded
*fatal* in its own docs. `agentcore-native` gives up the most but needs no
infrastructure at all, and is honest that the prompts which run are not the
prompts that were validated.

They are **different instruments, not one instrument at three quality levels.**
Running more than one is cheap and useful — cross-path disagreement isolates a
platform effect from a judge effect. Averaging their scores is not defensible.

Each folder carries:

| | |
|---|---|
| `README.md` | how to run it, what it costs |
| `architecture.html` | the data flow, one page |
| `judges.html` | every judge — checks, bright lines, score bands, grounding |
| `docs/TESTING.md` | testing strategy for that path |
| `docs/DESIGN.md`, `docs/LIMITS.md` | why this shape; what it gives up |
| `data/` | the 660 labelled samples and the hand-written fixtures |
| `tests/test_suite.py` | offline, no credentials needed |

## Status

**Proposed, not validated.** Prompt assembly, schema derivation, aggregation and
turn selection are tested. The rubrics themselves have never scored a real
transcript, and no agreement figure exists for any judge.

## Quick start

```bash
python3 -m venv .venv && .venv/bin/pip install -r harness/requirements.txt
.venv/bin/python -m harness.validate_suite                    # static checks
python3 tools/check_vendoring.py                              # all four implementations in sync?
.venv/bin/python -m harness.run_judges --input data/gold/examples.jsonl --dry-run
```

Scoring needs `ANTHROPIC_API_KEY` or an `ant auth login` profile.

```bash
.venv/bin/python -m harness.run_judges --input data/gold/examples.jsonl --k 5 --out runs/seed.jsonl
```
