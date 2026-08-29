# Runbook

Ordered. Steps 1–2 are cheap probes that can invalidate the whole plan; do them
before writing anything else.

Nothing before step 5 touches your tenant.

---

## 1. Settle the Anthropic thinking question — **gates everything**

Create one throwaway template evaluator on `claude-opus-5` with `max_tokens`
16000, run it once via **Test Evaluator**, and inspect token usage on the
Anthropic side for reasoning tokens.

`InvocationParamsRequest` has no Anthropic reasoning control (verified against
the 8.50.0 wheel), so the schema says the answer is probably no. The suite runs
every judge at `effort: high` today.

- **Thinking on by default** → proceed; this drops to a minor caveat.
- **Off** → decide between escalating to Arize for an Anthropic reasoning
  parameter, and moving `capability-honesty-traced` and `conduct-adaptation` to a
  model whose `reasoning_effort` AX can set — which is a judge-model change
  needing a full anchor re-baseline.

Do not build first and discover this later. See [LIMITS.md](LIMITS.md).

## 2. Settle the brace style

Arize's REST schema shows `{var}`; the `ax` CLI and Arize's own evaluator skill
show `{{var}}`. **A half-converted template does not error** — it renders the
variable as literal text and the judge silently scores a placeholder.

Create two throwaway evaluators, one with `{probe}` and one with `{{probe}}`, map
both, and read the Data Preview. Then pin the answer:

```bash
bin/arize-judges render --judge signal-density --brace-style single | head -40
```

## 3. Review the compiled templates offline

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m tests.test_suite          # 40+ offline checks, no account needed
bin/arize-judges list
bin/arize-judges check-drift                  # vendored rubrics vs ../judges
bin/arize-judges render --out build/          # one .txt per judge, hashed
```

## 4. Review the exact API payloads — still no network

```bash
bin/arize-judges plan --space SPACE_ID --integration INTEGRATION_ID | head -60
```

Read one `template_config` in full. This is byte-for-byte what would be sent.

## 5. Instrument the chatbot, ship to staging

Follow [INSTRUMENTATION.md](INSTRUMENTATION.md). Get ~50 real conversations into
a **staging** project carrying `attributes.judge.*`. Confirm the transcript
attribute arrives intact and un-truncated — that is the open question in that
doc.

## 6. Build ONE evaluator — the hardest one

`capability-honesty-traced`: largest payload, most extra fields, most likely to
be truncated or dropped.

```bash
bin/arize-judges provision --space SPACE --integration INT --traced --apply
```

Then **Test Evaluator** against one staging span and check:

- the explanation comes back as exactly one parseable JSON object
- all 7 checks are present with the declared ids
- `x.trace_claims` is populated in both directions
- the label is one of `1.0` / `0.5` / `0`

## 7. Backfill ~100 staging spans and measure

Windows must **end at least 2 hours in the past** — the eval index lags 1–2h and
a "now" window completes with zero spans. Max 10,000 items per triggered run;
evals only attach to spans ≤14 days old.

```bash
bin/arize-judges export --space SPACE --project staging --hours 24 --out checks.jsonl
```

Go/no-go on the encoding, with real numbers: payload parse rate, check-count
correctness, payload size against the 10k budget, and whether the span drawer
renders a ~3 KB single-line JSON legibly.

## 8. Provision the rest and stand up the export job

```bash
bin/arize-judges provision --space SPACE --integration INT --apply
bin/arize-judges tasks --project PROD            # review
bin/arize-judges tasks --project PROD --apply
```

Commit `evaluators.lock.json`. Schedule `export` nightly — it is what makes
per-check questions answerable at all, plus the two judge-integrity metrics.

## 9. Port validation, in two attributable stages

This is the step that tells you whether the port preserved the judges.

- **Stage (i)** — run the *rendered AX template text* through the existing
  Anthropic harness on the frozen anchor set. Measures **the prompt diff alone**
  against the parent's ship gate: <10% level change, and no change in which
  bright lines fire.
- **Stage (ii)** — run the identical text natively in AX on the same set.
  Measures **the platform effect**.

If (ii) fails while (i) passes, the culprit is the platform — formatter, forced
tool shape, or missing thinking — and step 1's answer tells you which.

## 10. Re-baseline the red team

Re-run the injection suite natively and re-measure verdict-flip and
rationale-manipulation attack success. Those are platform-dependent numbers now;
the harness figures do not transfer.

## 11. Go continuous

Two tasks: `critical` at 100%, `directional` at 10%. Backfill ~100 spans each,
verify, then flip `is_continuous`. Add a monitor per bright-line judge (TRACING
monitor, COUNT metric, filter eval label EQUALS `0`, threshold > 0).

Add a reconciliation job asserting every `evaluator_version_id` in
`GET /v2/tasks/{id}` is non-null and matches the lockfile — deleting a version
un-pins a running task and returns 200.
