# Helpfulness Judges

Ten independent prompt-based LLM judges for a retail-investment chatbot —
empathy, emotional intelligence, completeness, actionability, clarity, effort,
accommodation and honesty — plus one variant judge.

Two readmes, both HTML, both opened in a browser:

- **[README.interactive.html](README.interactive.html)** — filter the suite,
  toggle checks to watch a level resolve, quiz yourself against the 660 labelled
  samples, and browse the corpus. Self-contained; no server needed.
- **[README.html](README.html)** — the static reference. Same suite table and a
  worked pass / warning / hard-failure example per judge, in reading order.

This file is a short index so the repo is navigable from a terminal. It is
deliberately thin; everything substantive lives in the documents below.

| | |
|---|---|
| [README.interactive.html](README.interactive.html) | Interactive readme — filters, live level simulator, calibration quiz, corpus browser. |
| [README.html](README.html) | Static readme. Suite table, per-judge descriptions, worked examples. |
| [docs/whitepaper.md](docs/whitepaper.md) | Construct-by-construct rationale, design decisions, full bibliography (77 refs). |
| [docs/literature-review.md](docs/literature-review.md) | The underlying review, with citation-confidence flags. |
| [docs/methodology.md](docs/methodology.md) | Every prompt-architecture decision and its source. |
| [docs/validation-protocol.md](docs/validation-protocol.md) | Gold set, agreement targets, drift monitoring, ship gates — the standard. |
| [docs/proposed-testing.md](docs/proposed-testing.md) | What to test first, what it costs, and what we already know without running anything — the plan. |
| [judges/](judges/) | The prompts — the source of truth all three paths derive from. |
| [harness/](harness/) | Loader, transcript renderer, turn policy, runner, validators. |
| [data/](data/) | Hand-written fixtures, plus [660 labelled test samples](data/testsets/README.md) — 60 per judge, 20 per level. |


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
.venv/bin/python -m harness.run_judges --input data/gold/examples.jsonl --dry-run
```

Scoring needs `ANTHROPIC_API_KEY` or an `ant auth login` profile.

```bash
.venv/bin/python -m harness.run_judges --input data/gold/examples.jsonl --k 5 --out runs/seed.jsonl
```
