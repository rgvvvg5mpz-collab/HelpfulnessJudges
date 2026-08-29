# What running natively costs

Every item was verified against the installed `arize` **8.50.0** wheel — model
fields, enums, regexes and constants read from source, not from documentation.
The two disagree in places, and where they do, the wheel wins.

Graded **fatal** (may change whether you should do this at all) → **major**
(costs a real capability) → **minor** (annoying, mitigable).

---

## FATAL — Anthropic extended thinking is unreachable

`InvocationParamsRequest` exposes exactly three reasoning controls:

| field | provider |
|---|---|
| `thinking_level` | Gemini 3.x |
| `thinking_budget` | Gemini 2.5 |
| `reasoning_effort` | OpenAI o-series / GPT-5 |

**There is nothing for Anthropic.** `ProviderParamsRequest.anthropic_headers`
carries only `anthropic_beta` feature flags. The parent suite runs every judge at
Anthropic `effort: high`, and sizes `max_tokens=16000` explicitly "for the
thinking, not for the output."

The two judges that lose most are the two doing the hardest reasoning:
`capability-honesty-traced`, which walks a turn building `trace_claims` and then
walks the trace the other way for `unreported_work`; and `conduct-adaptation`,
whose central test is counterfactual — *would this conversation have gone the
same way without the signal?*

**Do not build first and discover this later.** It is step 1 of the runbook.

Mitigations, in order:
1. **Test whether AX's Anthropic adapter enables adaptive thinking by default**
   on `claude-opus-5` and just needs headroom. Set `max_tokens` high and inspect
   token usage on the Anthropic side for reasoning tokens. If thinking is on by
   default this drops to **minor** and everything else here stands.
2. **Escalate to Arize.** An Anthropic reasoning parameter is a small, obviously
   correct schema addition, and they already carry the equivalent for two other
   providers.
3. **Last resort: move the two hardest judges to a model whose `reasoning_effort`
   AX can set.** That is a judge-model change, which requires a full anchor-set
   re-baseline and breaks the cross-family self-preference argument.

---

## MAJOR — no k=5, and no repetition primitive anywhere

There is no `repetitions` / `trials` / `n` field in the evaluator schema, the
task schema, the CLI or GraphQL. `sampling_rate` selects *which* records get
scored, never how many times each is scored. `TaskEvaluatorInput.evaluator_id`
states "Duplicates are not allowed", so one evaluator cannot be attached five
times. Re-running is actively blocked: when all matching data already has labels
the task cancels with zero successes, and `override_evaluations` **replaces** the
label rather than appending a second sample.

You lose `unanimous`, `hard_failure_votes`, and the split-across-the-failure-
boundary routing the parent design uses to decide whether to trust a verdict —
plus the §5 ship gate ("k=10, unanimous on ≥80%, zero items split across 0") and
the §6 dashboard metric "intra-judge repeat-run alpha".

**Do not simulate k=5 with 50 evaluators.** At `temperature: 0` the five
repetitions are degenerate; raise the temperature and you have changed the judge.

**Mitigation.** Split the concern honestly: AX runs k=1 for production trend and
bright-line alerting; k=5/k=10 stays offline against the frozen anchor set.
Repeat-run alpha becomes a weekly instrument-health measurement, not a production
tile. Say so explicitly in the validation memo — a reviewer will ask where the k
went.

---

## MAJOR — the payload is stored but not queryable

`Evaluation.__properties = ["name", "score", "label", "explanation"]`. The JSON
payload survives intact inside `explanation` (that part is *not* lost — see
DESIGN.md), but AX filter expressions support only equality, comparison and
AND/OR. No substring, no LIKE, no regex, no JSON extraction, and no AQL example
touches an `.explanation` column.

So **"show me every span where C4 failed" is unanswerable inside Arize** — not in
the Spans tab, not in dashboards, not in monitors.

**Mitigation.** `arize-judges export` is exactly this. It pulls the explanation
columns over Arrow Flight, validates each payload, expands the check vectors, and
lands a per-check table in your own store where that question is a `WHERE`
clause. This is a scheduled export, not a judge harness — Arize still owns model
invocation, sampling, cadence and write-back.

If per-check *alerting inside AX* is genuinely required for one or two checks,
buy it by promoting exactly those to their own evaluators and leave the other
~60 in the payload. Reserve that for bright lines that actually page someone.

---

## MAJOR — nothing validates the payload shape

On the Anthropic path the parent harness enforces the verdict with a JSON schema:
`additionalProperties: false`, `minItems == maxItems == len(check_ids)`, check
ids enumerated, verdicts real booleans, score restricted to three values.

Natively, everything except `label`/`score` is a model-authored string inside a
string, with no validator anywhere in the path. A judge that emits five checks
instead of seven, invents a check id, or writes `trace_support: "unclear"` fails
**silently** and lands on the span looking normal.

**Mitigation.** `arize_judges.verify` reconstructs the schema as a gate on the
export path, and turns the loss into two metrics you did not have before:
**payload validity rate** (a judge degrades here before its scores move) and
**label divergence rate** (the model's label vs what its own `bl` + `chk` imply).
Alarm on both.

---

## MAJOR — prompt caching and the Batch API do not transfer

`EvaluatorLlmConfigRequest` exposes `ai_integration_id`, `model_name`,
`invocation_parameters`, `provider_parameters`. There is no `cache_control`
anywhere, and `cache_control` is a message-block annotation, so it is
structurally unreachable — AX assembles the messages array.

The 132-line preamble is re-billed at full price on every judge call, for every
turn, for all 10 judges. The parent harness also supports `--batch` for the 50%
discount; that is gone too.

**Mitigation.** Sampling is the honest lever, and the two-tier split already
implements it: 100% for the bright-line judges, 10% for the relational judges the
validation protocol already restricts to directional use.

---

## MINOR — variant mutual exclusion is not enforced by Arize

The parent harness raises when you ask for a judge and its variant together. AX
has no equivalent: attaching both `capability-honesty` and
`capability-honesty-traced` silently double-counts the same defect.

**Mitigated here** — `spec.deployable()` drops one side, and a test asserts they
can never both appear.

---

## MINOR — `evaluator_version_id` can be un-pinned behind your back

Null means "always latest", so an Eval Hub edit silently changes what production
runs mid-window with no version boundary in the data. Worse,
`DELETE /v2/evaluators/{id}/versions` explicitly un-pins a running task and falls
back to latest, returning 200.

**Mitigated here** — `task_plan()` refuses to build a task with an unpinned
evaluator, and the lockfile is the manifest to reconcile against. Add a periodic
job that reads `GET /v2/tasks/{id}` and asserts every `evaluator_version_id`
matches `evaluators.lock.json`.

---

## MINOR — free-text fallback could shred the payload

If a judge model lacks tool calling, AX appends output instructions to the system
prompt and parses free text with a regex that searches for `label` and truncates
at `explanation`.

**Mitigated here** — the tail forbids those two key names inside the payload, so
even the fallback path degrades gracefully instead of corrupting label
extraction. Also pick a tool-calling model and leave `use_structured_output` at
its `True` default.

---

## MINOR — Turn Definition and subquery mapping are UI-only

`{turn_data}` is absent from `TemplateConfigInput.__properties` (whose
`from_dict` raises on extra keys), and `TaskEvaluatorInput.column_mappings` is a
flat `Dict[str, str]` with no per-variable subquery binding.

**Already avoided** by the pre-rendered-attribute design, which needs neither.
Record it as a reason *not* to drift toward session scope later: the moment
someone reaches for `{turn_data}`, two evaluators leave version control
permanently.
