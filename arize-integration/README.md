# Arize integration

Deploy the helpfulness judge suite as **native Arize AX evaluators** — Arize calls
the model, on a continuous task, against your production traces.

Standalone. This folder does not import from the parent repository and does not
modify it. The rubrics are vendored under `rubrics/` with hashes, so drift from
the reviewed originals is detectable rather than silent.

> **Status: designed and tested offline, never run against a tenant.** Every API
> shape here was verified by reading the installed `arize` **8.50.0** wheel —
> model fields, enums, regexes, constants — because the published docs disagree
> with the shipped SDK in several places. No call has been made to Arize.

---

## The short version

| | |
|---|---|
| **Shape** | 10 template evaluators (+1 variant), all at **SPAN** scope, on two tasks split by sampling rate |
| **Scale** | `classification_choices = {"1.0": 1.0, "0.5": 0.5, "0": 0.0}` — native, three levels |
| **Structured output** | The full check vector rides as JSON inside the eval `explanation` |
| **Conversation input** | A pre-rendered `attributes.judge.transcript` your chatbot writes |
| **Provisioning** | Idempotent Python + a committed lockfile. There is no declarative config surface in Arize — no Terraform, no `apply -f` |

**The good news** is bigger than expected. The forced tool's `explanation`
parameter is an unconstrained string, and it is required **before** `label` — so
the payload is generated as chain-of-thought and the score is conditioned on it,
which is exactly the ordering the rubrics already demand. A realistic
`capability-honesty-traced` payload is **1,523 characters** against a 10,000
ceiling. The structure is *not* lost; it stops being **queryable**, which
`arize-judges export` exists to fix.

**The bad news is one thing, and it is load-bearing.** `InvocationParamsRequest`
carries `thinking_level` (Gemini), `thinking_budget` (Gemini) and
`reasoning_effort` (OpenAI) — and **nothing for Anthropic**. The suite runs every
judge at `effort: high`. Settle that before building anything: step 1 of the
[runbook](docs/RUNBOOK.md).

---

## Quick start — nothing here touches your tenant

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m tests.test_suite       # 40+ offline checks
bin/arize-judges list                      # the suite, and what deploys
bin/arize-judges check-drift               # vendored rubrics vs ../judges
bin/arize-judges render --out build/       # compiled templates, hashed
bin/arize-judges plan --space SPACE --integration INT | head -60
```

`list`, `check-drift`, `render`, `plan` and `verify` need only **PyYAML**. They
work with no Arize account, no API key and no network — which is how you review
the entire deployment before it exists.

## Deploying

Needs `pip install arize pandas` and `ARIZE_API_KEY`.

```bash
bin/arize-judges provision --space SPACE --integration INT          # dry run
bin/arize-judges provision --space SPACE --integration INT --apply
bin/arize-judges tasks --project PROD --apply
bin/arize-judges export --space SPACE --project PROD --hours 24 --out checks.jsonl
```

`tasks` reads `evaluators.lock.json`, so `provision --apply` must run first — it
is what creates the evaluator and version ids the task pins to. Commit the
lockfile; it is the manifest CI reconciles against.

Follow [docs/RUNBOOK.md](docs/RUNBOOK.md) in order — steps 1 and 2 are cheap
probes that can invalidate the plan.

## Layout

```
rubrics/                    vendored judges + preamble, with provenance hashes
  _scoring-tail-ax.md       the ONLY rewritten file — the AX output contract
arize_judges/
  spec.py                   standalone rubric loader + drift check
  render.py                 rubric -> AX template string (hashed build artifact)
  naming.py                 judge id -> AX eval name (the silent-drop trap)
  provision.py              idempotent reconcile, lockfile, dry-run
  verify.py                 payload validation + label recomputation
  export.py                 read evals back, expand check vectors
  instrument.py             what the chatbot must emit
  cli.py                    arize-judges
docs/
  DESIGN.md                 why this shape; rejected alternatives and evidence
  LIMITS.md                 what native execution costs, graded fatal/major/minor
  INSTRUMENTATION.md        the tracing contract
  RUNBOOK.md                ordered deployment steps
examples/                   a worked payload and span attributes
tests/test_suite.py         offline, no account required
```

## What changes in the rubrics

**One file.** `_scoring-tail-ax.md` replaces `_scoring-tail.md`, because the
output contract genuinely differs: the payload now rides in the `explanation`
argument and the score becomes the `label`.

`_preamble.md` and all eleven rubric bodies are used **byte-identical**. That is
the payoff of using `"1.0"/"0.5"/"0"` as the classification choices rather than
`fail`/`warn`/`pass` — the scale language and every Score section survive
untouched, so the anchor-set diff is one file rather than twelve.

The rubrics are never hand-edited in the Eval Hub. `render` compiles them into a
hashed build artifact; anything else guarantees the git rubrics and the deployed
rubrics diverge within a month with nobody able to say which produced a score.

## What this buys you that the parent harness does not

A new payload field, `bl` — **the name of the bright line that fired**, or null.
The parent JSON schema has no such field, so the 0-vs-0.5 decision is unauditable
model arithmetic. With `bl` plus the check vector, `verify.recompute_label`
derives the label the mapping rule *implies* and compares it to what the model
chose. Disagreement means the judge is not applying its own rule.

`export` reports that as **label divergence rate**, alongside **payload validity
rate** — a judge degrades there before its scores move. Neither metric exists on
the Anthropic path.

## Known limits

Read [docs/LIMITS.md](docs/LIMITS.md) before committing. In severity order:

1. **Anthropic extended thinking is unreachable** — fatal until disproven.
2. **No k=5**, no repetition primitive anywhere. Keep k=5 offline on the anchor set.
3. **The payload is stored but not queryable** in Arize. `export` is the answer.
4. **Nothing validates the payload** natively. `verify` reconstructs the schema.
5. **No prompt caching, no Batch API.** Sampling is the honest cost lever.

## Open questions to confirm against your tenant

1. Does AX's Anthropic adapter enable extended thinking on `claude-opus-5`?
2. Single-brace `{var}` or double-brace `{{var}}`? A half-converted template does
   not error — it scores a literal placeholder.
3. Is there an ingest cap on a single span attribute? A full transcript can run
   20–100 KB.
4. Does a ~1.5 KB JSON explanation survive end to end and render legibly in the
   span drawer? The mechanism is verified; no Arize example does this, so you
   would be first.
5. What temperature does AX apply when `invocation_parameters` is `{}`? There is
   no documented default; we set it explicitly on all eleven.
6. Is the space Enterprise? Custom code evaluators are gated, which is the only
   thing standing between you and an in-Arize deterministic re-scoring pass.

---

## This folder is one of three

The same ten judges deploy three ways. This is **Arize AX**. The siblings are
[`../agentcore-integration`](../agentcore-integration) and [`../agentcore-native`](../agentcore-native); the comparison and the choice between
them is in [`../README.md`](../README.md).

Each folder is standalone and duplicates what it needs — rubrics, test data,
docs, diagrams. That is deliberate: you should be able to hand any one of them to
a team without the others.

**In this folder:** [architecture.html](architecture.html) ·
[judges.html](judges.html) · [docs/TESTING.md](docs/TESTING.md) ·
[docs/DESIGN.md](docs/DESIGN.md) · [docs/LIMITS.md](docs/LIMITS.md) ·
[data/](data/)
