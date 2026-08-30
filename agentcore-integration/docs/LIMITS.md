# What this costs

Verified against botocore `1.43.83` service models for `bedrock-agentcore-control`
and `bedrock-agentcore` — enums, patterns and numeric bounds read from source.

Graded **fatal** → **major** → **minor**. Compare against
[COMPARISON.pdf](COMPARISON.pdf), which sets these beside the Arize equivalents.

---

## MAJOR — the 300-second Lambda ceiling, and a 6 MB input cap

`lambdaConfig.lambdaTimeoutInSeconds` has bounds `min=1, max=300`. Everything the
judge does happens inside that: parsing `sessionSpans`, rendering the transcript,
and **five concurrent Opus calls at `effort: high` on a ~19k-token prompt**.

AgentCore also warns that `sessionSpans` "may be truncated if the original
payload exceeds 6 MB" — a long conversation with verbose tool results can
approach that.

**This is the top sizing risk and the only limit that can force a redesign.**
Measure it before building anything else. Fallbacks, in order:

1. Drop to **k=3** in production and keep k=5/k=10 offline on the frozen anchor
   set. (The Arize path runs k=1, so k=3 is still ahead.)
2. Have the Lambda **fetch the transcript from your own store** by conversation
   id rather than parsing megabytes of spans.
3. **Split the two hardest judges** — `capability-honesty-traced` and
   `conduct-adaptation` — into their own evaluators with separate budgets.

## MAJOR — Transaction Search is an account-wide prerequisite

Nothing works until CloudWatch Transaction Search is enabled for the account, and
the chatbot emits conforming OTel spans with `session.id`. AgentCore reconstructs
sessions from spans; it will not accept a transcript blob.

This has a **cost implication** (billed on ingested spans) and is an account-wide
change, so it needs agreement before anyone runs it.

**Mitigation.** Prove the whole seam first with the on-demand `Evaluate` API and
hand-made spans, which needs none of it. See [RUNBOOK.md](RUNBOOK.md) step 3 and
[CLOUDWATCH.md](CLOUDWATCH.md).

## MAJOR — the verdict still collapses to three fields

A code-based evaluator returns exactly `label`, `value`, `explanation`. The check
vector, evidence spans, `trigger_present`, `confidence` and every per-judge extra
have no first-class home.

**Mitigation, and it is better here than on the Arize path.** The payload is
serialised into `explanation` — but because the Lambda calls Anthropic with
`output_config.format` json_schema, it was **schema-validated at generation time**
before serialisation. Arize's equivalent has nothing validating the payload at
all. `verify.py` then checks the serialisation and recomputes the label.

## MINOR — no repetition primitive in the service

`samplingPercentage` selects *which* sessions get scored, never how many times.
Batch evaluation reports `statistics.averageScore` — a mean, no dispersion.

**Minor only because the Lambda absorbs it**: k=5, the median, the spread and the
review-routing decision all happen inside the handler, and the aggregate is what
gets returned. On the native `llmAsAJudge` path this would be major, exactly as
it is for Arize.

## MINOR — an evaluator is locked while its config is ENABLED

A rubric edit against a live config fails mid-deploy.

**Mitigation.** Bake pause → update → resume into the deploy job, or version
evaluators by cloning under a new name — accepting that an `EvaluatorName` change
forces CloudFormation *Replacement* and a new ARN.

## MINOR — 10 evaluators per online config, non-adjustable

The suite fits with **zero headroom**, and only because variant mutual exclusion
drops one side of the honesty pair.

**Mitigation.** A test asserts the count, so an eleventh judge surfaces as a test
failure rather than a runtime `ValidationException`. The two-tier sampling split
wants two configs anyway, which incidentally buys headroom.

## MINOR — no Batch API discount on the online path

Online evaluation is event-driven, one Lambda invocation per session.

**Mitigation.** Prompt caching is the bigger lever anyway: k=5 repeats an
identical prefix five times and all ten rubrics share one 132-line preamble.
Keep `--batch` for offline anchor runs, where it works untouched.

## MINOR — the native LLM-judge path is unusable for this suite

Recorded so nobody re-proposes it on simplicity grounds. `llmAsAJudge` appends
its own standardisation prompt forcing `{reasoning, score}`, instructs you not to
write output-format instructions, and restricts prompts to a closed placeholder
set — `{context}`, `{assistant_turn}`, `{tool_turn}`, `{available_tools}` — with
no custom-variable mechanism. The rubrics name their transcript markers
explicitly, so a service-formatted `{context}` in an undocumented shape silently
drifts every rubric's meaning. Its `inferenceConfig` also exposes only
maxTokens/temperature/topP: no cache control and no schema.

Reasoning control on that path is **unverified rather than absent** — see
`../agentcore-native/docs/LIMITS.md`. That correction does not change the
recommendation, because the output contract and placeholder constraints are the
binding ones.

---

## Not a limit: what this path keeps that Arize loses

Worth stating, because it is the reason for the design.

| | Arize AX | AgentCore (code-based) |
|---|---|---|
| Schema enforcement | none | full, at generation time |
| Anthropic `effort: high` | unreachable | native |
| Prompt caching | unreachable | preserved |
| k=5 + median + spread | blocked | preserved |
| Tool-call reach | span scope cannot | raw `sessionSpans` |
| Secrets for a custom judge | plaintext in the Eval Hub | Lambda role + Secrets Manager |
