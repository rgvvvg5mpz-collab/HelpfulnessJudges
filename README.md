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
| [docs/validation-protocol.md](docs/validation-protocol.md) | Gold set, agreement targets, drift monitoring, ship gates. |
| [judges/](judges/) | The prompts. One flat folder; `_`-prefixed files are shared scaffolding. |
| [harness/](harness/) | Loader, transcript renderer, turn policy, runner, validators. |
| [data/](data/) | Hand-written fixtures, plus [660 labelled test samples](data/testsets/README.md) — 60 per judge, 20 per level. |

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
