# CloudWatch setup

**Nothing works until this is done.** AgentCore Evaluations reads spans *from
CloudWatch*, reconstructs sessions from them, and hands those sessions to your
evaluator. If the traces are not in CloudWatch in the right shape, the judges
have nothing to score — and the failure looks like "the judges found no
problems", which is the worst possible failure mode for an eval suite.

Budget this as its own workstream, separate from the judge work. It is plausibly
the larger half of the integration.

---

## 1. Enable Transaction Search — account-wide, once

AgentCore Evaluations depends on CloudWatch Transaction Search, which sends
spans to the `aws/spans` log group and makes them queryable.

```bash
# Let X-Ray write spans into CloudWatch Logs
aws logs put-resource-policy \
  --policy-name TransactionSearchXRayAccess \
  --policy-document '{
    "Version": "2012-10-17",
    "Statement": [{
      "Effect": "Allow",
      "Principal": {"Service": "xray.amazonaws.com"},
      "Action": "logs:PutLogEvents",
      "Resource": [
        "arn:aws:logs:REGION:ACCOUNT:log-group:aws/spans:*",
        "arn:aws:logs:REGION:ACCOUNT:log-group:/aws/application-signals/data:*"
      ],
      "Condition": {
        "ArnLike":      {"aws:SourceArn":     "arn:aws:xray:REGION:ACCOUNT:*"},
        "StringEquals": {"aws:SourceAccount": "ACCOUNT"}
      }
    }]
  }'

# Route 100% of spans to CloudWatch Logs
aws xray update-trace-segment-destination --destination CloudWatchLogs
aws xray update-indexing-rule \
  --name "Default" \
  --rule '{"Probabilistic": {"DesiredSamplingPercentage": 100}}'
```

Verify:

```bash
aws xray get-trace-segment-destination      # Destination: CloudWatchLogs, Status: ACTIVE
```

> **This is an account-wide change with a cost implication** — Transaction Search
> is billed on ingested spans. Get it agreed before running it, and prove the
> rest of the pipeline first with the on-demand `Evaluate` API and hand-made
> spans (see [RUNBOOK.md](RUNBOOK.md) step 3), which needs none of this.

## 2. What the chatbot must emit

AgentCore reconstructs conversations from OpenTelemetry spans. It will **not**
accept a transcript blob — this is the structural difference from the Arize
integration, which reads a pre-rendered attribute off a single span.

### Required on every span

| Attribute | Why |
|---|---|
| `session.id` | **Sessions do not exist without it.** SESSION-level judges score nothing, and TRACE-level judges lose their conversation context. |
| `service.name` | Must match a `serviceNames` entry in the online evaluation config, or the config matches no data. |

### Per LLM span (one per assistant turn)

| Attribute | Read by |
|---|---|
| `openinference.span.kind = "LLM"` *(or `gen_ai.operation.name`)* | `spans.py::_kind` — how a turn is recognised |
| `input.value` *or* `gen_ai.prompt` | the customer turn |
| `output.value` *or* `gen_ai.completion` | the assistant turn |

### Per TOOL span — this is where `capability-honesty-traced` lives or dies

| Attribute | Read by |
|---|---|
| `openinference.span.kind = "TOOL"` | tool recognition |
| `tool.name` *or* `gen_ai.tool.name` | the call name |
| `tool.parameters` *or* `gen_ai.tool.arguments` | the args summary |
| `output.value` *or* `gen_ai.tool.result` | the returned value |
| `status.code` **and** `status.message` | **failure disclosure** |

**The status is as important as the result.** Check C5 of
`capability-honesty-traced` asks whether the turn disclosed a failed lookup
rather than answering around it — and it fails *silently* if the trace only ever
shows successes. `spans.py::_tool_entry` renders `-> ERROR <message>` for a
failed span and `-> EMPTY` for an empty result, and the judge branches on both.

Wrap actual tool **execution** in a TOOL span yourself. Auto-instrumentors trace
the model's *request* for a tool call, not your function running.

### Trace grouping

One trace = one request/response turn. `spans.py` groups spans by `traceId`
before reconstructing, so **a tool span must carry the trace id of the turn it
belongs to.** Getting this wrong reconciles a turn's claims against a different
turn's tool calls — precisely the failure the traced judge exists to detect.

## 3. Log groups

AgentCore Runtime writes to:

```
/aws/bedrock-agentcore/runtimes/<agent-id>-<endpoint>
```

Those names go in `dataSourceConfig.cloudWatchLogs.logGroupNames`. For a
non-AgentCore-hosted chatbot, use whatever log group receives its OTel spans.

Evaluation **results** are written by AgentCore to a different place:

```
/aws/bedrock-agentcore/evaluations/results/<online-evaluation-config-id>   # online
/aws/bedrock-agentcore/evaluations/batch                                   # batch
```

as `gen_ai.evaluation.result` events **parented to the evaluated span**, with
`gen_ai.evaluation.score.value`, `.score.label` and `.explanation`. Metrics are
auto-extracted into the `Bedrock-AgentCore/Evaluations` namespace and surface in
the console under **GenAI Observability → Bedrock AgentCore → Evaluations**.

## 4. Querying the check vector

This is where CloudWatch beats Arize. Arize's filter expressions are equality and
comparison only, so "every span where C4 failed" is unanswerable in-platform.
Logs Insights has `parse`, regex `filter` and JSON access:

```sql
fields @timestamp, `attributes.gen_ai.evaluation.name` as judge,
       `attributes.gen_ai.evaluation.score.value` as score,
       `attributes.gen_ai.evaluation.explanation` as payload
| filter judge = 'signal_density'
| parse payload /"id":"C4","ok":(?<c4>true|false)/
| filter c4 = "false"
| sort @timestamp desc
```

Items the handler flagged for human review — spread across the 0 boundary, low
confidence, or suspected injection:

```sql
fields @timestamp, `attributes.gen_ai.evaluation.name` as judge,
       `attributes.gen_ai.evaluation.explanation` as payload
| parse payload /"review":(?<review>true|false)/
| filter review = "true"
```

**Confirm this empirically before relying on it.** The mechanism is sound but no
AWS example puts structured JSON in `explanation`, so you would be first — same
caveat as the Arize sibling.

## 5. IAM

Two roles, and they are easy to conflate:

**Evaluation execution role** (`evaluationExecutionRoleArn` on the online config)
— assumed by AgentCore to read your spans and invoke your Lambda:

```json
{"Version": "2012-10-17", "Statement": [
  {"Effect": "Allow",
   "Action": ["logs:StartQuery", "logs:GetQueryResults", "logs:FilterLogEvents",
              "logs:DescribeLogGroups"],
   "Resource": "arn:aws:logs:REGION:ACCOUNT:log-group:*"},
  {"Effect": "Allow", "Action": "lambda:InvokeFunction",
   "Resource": "arn:aws:lambda:REGION:ACCOUNT:function:helpfulness-judges"}
]}
```

with a trust policy for `bedrock-agentcore.amazonaws.com`.

**Lambda execution role** — needs CloudWatch Logs for its own output, and read
access to wherever the Anthropic API key lives:

```json
{"Effect": "Allow", "Action": "secretsmanager:GetSecretValue",
 "Resource": "arn:aws:secretsmanager:REGION:ACCOUNT:secret:anthropic/judge-key-*"}
```

**The API key never goes in the evaluator config.** This is the asymmetry that
makes AgentCore a better host than Arize: Arize's code evaluators have no secret
store, so a key would sit in plaintext in the Eval Hub. A Lambda has a role.

## 6. Checklist

- [ ] Transaction Search enabled; `get-trace-segment-destination` returns ACTIVE
- [ ] `session.id` on every span
- [ ] `service.name` matches the online config's `serviceNames`
- [ ] LLM spans carry input and output values
- [ ] TOOL spans carry name, params, result **and status**
- [ ] Tool spans share the trace id of their turn
- [ ] Log group names identified for `dataSourceConfig`
- [ ] Both IAM roles created, with trust policies
- [ ] API key in Secrets Manager, Lambda role granted read
- [ ] `Evaluate` proves the seam on hand-made spans before anything goes account-wide
