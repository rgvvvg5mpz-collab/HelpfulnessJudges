# Runbook

Ordered. Steps 1–3 are cheap and can invalidate the plan; do them first.
Nothing before step 5 touches AWS.

## 1. Read the CloudWatch requirements

[CLOUDWATCH.md](CLOUDWATCH.md). Establish whether the chatbot already emits
conforming OTel spans with `session.id`, and whether Transaction Search is
enabled or would need approval. **This is plausibly the larger half of the work**
and it gates everything downstream.

## 2. Review everything offline

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m tests.test_suite            # 51 checks, no credentials
bin/agentcore-judges list
bin/agentcore-judges check-drift
bin/agentcore-judges render --judge capability-honesty-traced
bin/agentcore-judges validate --lambda-arn ARN  # strict: enums, patterns, bounds
```

`validate` catches what `botocore.validate` does not — enum values, string
patterns and numeric bounds. A hyphenated evaluator name or a 900-second timeout
fails here rather than at deploy.

## 3. Prove the seam with `Evaluate`, before enabling anything account-wide

The on-demand `Evaluate` API takes inline `sessionSpans`, so it needs no
Transaction Search, no online config and no live traffic. Hand-build a few spans
(`examples/session_spans.json`), point one evaluator at them, and confirm the
round trip.

This is the cheapest possible proof that the payload survives and the transcript
reconstructs correctly.

## 4. Settle the sizing question — **the one that can force a redesign**

k=5 concurrent Opus calls at `effort: high` on a ~19k-token prompt must finish
inside 300 seconds. Time it against a real transcript before committing.

If it does not fit: drop to k=3 in production and keep k=5/k=10 offline on the
anchor set. Decide this now, not after the Lambda is deployed.

## 5. Build and deploy the Lambda

Package `agentcore_judges/` plus `rubrics/` plus the `anthropic` SDK. Give the
execution role Secrets Manager read for the API key — **the key never goes in the
evaluator config.**

Set `JUDGE_MODEL`, `JUDGE_EFFORT`, `JUDGE_K` and `JUDGE_MAX_TOKENS` as environment
variables so a change does not need a rubric edit.

## 6. Create one evaluator — the hardest one

`capability-honesty-traced`: largest payload, most extra fields, and the only
judge whose correctness depends on tool spans being attributed to the right turn.

```bash
bin/agentcore-judges provision --lambda-arn ARN            # dry run
bin/agentcore-judges provision --lambda-arn ARN --apply
```

Then run `Evaluate` against a staging session and check: the explanation parses
as one JSON object, all 7 checks are present, `x.trace_claims` is populated in
both directions, and the label is one of `1.0` / `0.5` / `0`.

## 7. Enable Transaction Search and instrument

Only now, and only with the account-wide change agreed. [CLOUDWATCH.md](CLOUDWATCH.md)
§1–§2. Ship to a staging project and confirm spans arrive with `session.id` and
tool status.

## 8. Provision the rest and create the online configs

```bash
bin/agentcore-judges provision --lambda-arn ARN --apply
bin/agentcore-judges online-config --tier critical \
    --log-group /aws/bedrock-agentcore/runtimes/chat --service chatbot \
    --role ROLE_ARN --apply          # leave --enable off
```

Backfill and inspect before going continuous. Commit `evaluators.lock.json`.

## 9. Port validation, in two attributable stages

The step that tells you whether the port preserved the judges.

- **Stage (i)** — run the *unchanged rubrics* through the parent Anthropic
  harness on the frozen anchor set. Since no rubric file changed, this should
  reproduce the existing baseline exactly. A difference here means the vendored
  copies drifted; `check-drift` should have caught it.
- **Stage (ii)** — run the same set through the **Lambda**, via `Evaluate`. This
  measures the platform effect: span reconstruction, k inside a time budget, and
  the payload round trip.

Stage (i) passing while (ii) fails isolates the fault to this integration rather
than the rubrics.

## 10. Go continuous

Enable the critical tier, then the directional tier. Add CloudWatch alarms on the
`Bedrock-AgentCore/Evaluations` namespace for each bright-line judge — alarm on
label `0` count greater than zero.

Schedule the payload-validity and label-divergence queries from
[CLOUDWATCH.md](CLOUDWATCH.md) §4 as dashboards. A judge degrades on payload
validity before its scores move.
