# Design

Why this shape, and which alternatives were rejected on what evidence.

Every API claim was verified against the installed `arize` **8.50.0** wheel by
reading model fields, enums, regexes and constants from source. Where the
published docs disagree with the wheel, the wheel wins — and they do disagree.

---

## The one decision everything follows from

**All eleven judges are SPAN-scope template evaluators, each reading a single
pre-rendered `attributes.judge.transcript` attribute the chatbot writes.**

Not `{conversation}`, not `{turn_data}`, not multi-span queries. That one choice
collapses the scope, aggregation and tool-reach problems simultaneously.

### Why not trace scope for `capability-honesty-traced`

`TaskEvaluatorInput.column_mappings` is `Dict[str, StrictStr]` — flat, with no
per-variable subquery field. The "subquery-aware variable mapping" documented for
trace/session scope is **UI-only and unscriptable**.

Even in the UI it would deliver tool names and tool results as two independent
comma-joined lists in chronological order, with no call-to-result pairing — so
the judge would have to *guess* which result belongs to which call. That guess is
precisely the reconciliation checks C2/C3/C5 exist to perform rigorously. Trace
scope buys nothing here and destroys pairing.

### Why not session scope for the two conversation judges

`{turn_data}` and its Turn Definition are absent from
`TemplateConfigInput.__properties`, whose `from_dict` raises on extra keys. Those
two evaluators would be hand-built in the UI: unversionable, unreproducible from
this repo, and still unable to reproduce the transcript format the rubrics
reference by name.

Session scope also writes **one** result to the root span of the session's *first*
trace, and truncates each value at 100,000 chars before capping the assembled
session at 100,000 chars — per-value truncation runs first, so a verbose early
turn silently eats the budget and the latest turns vanish. A judge scoring "did
this conversation reach resolution" that cannot see the end of it is worse than
no judge.

So both conversation judges stay at SPAN scope too, reading
`attributes.judge.conversation_transcript` written on the final assistant turn.

### The honest cost

Arize is no longer assembling the conversation — your instrumentation is. The
chatbot's tracing layer becomes part of the eval contract. That is real coupling,
and it is still strictly better than every AX-assembled alternative.

---

## The scale: numerals as labels

```python
classification_choices = {"1.0": 1.0, "0.5": 0.5, "0": 0.0}
```

The label strings are the numerals themselves, not `fail`/`warn`/`pass`. This is
the highest-leverage small choice in the port.

Because the labels *are* the numerals, `_preamble.md`'s entire scale section —
"Three levels, and only three. Never emit any other value" — and every rubric's
Score section stay **byte-identical** to the reviewed originals. The anchor-set
diff shrinks to one file. Had we used `fail`/`warn`/`pass`, every rubric's Score
section would need rewriting and the whole suite would need re-validation.

Arize enforces the three as a tool enum and writes `.score` = 1.0/0.5/0.0
alongside the string `.label`.

**Caveat worth stating in any report.** These three are not an ordinal ramp — the
preamble says the 0.5→0 distance is much larger than 1.0→0.5, and 0 fires only on
a named bright line. Arize's dashboards average scores, so a bright-line failure
renders as "slightly worse than a warning". Never publish a bare mean without the
hard-failure rate beside it.

---

## The payload rides in `explanation`

This is the finding that makes native execution viable rather than merely
possible.

Arize AX runs the Phoenix evals library. In `generate_classification_schema`,
with `include_explanation=True`:

```python
properties["explanation"] = {"type": "string",
                             "description": "A brief explanation of your reasoning."}
required.append("explanation")
properties["label"] = label_schema        # enum of the three choices
required.append("label")
```

Three things follow, all load-bearing:

1. **`explanation` is an unconstrained string** — no `maxLength`, no `pattern`,
   no schema. A compact JSON object emitted into it survives verbatim.
2. **It is required *before* `label`.** The payload is generated as
   chain-of-thought and the label is conditioned on it — exactly the ordering the
   rubrics already demand ("write the reasoning before choosing the score").
3. **Nothing reads the prompt to build the tool**, so prescribing the
   explanation's *content* cannot break the forced call.

Arize's docs say "Leave labels and response format out of your prompt." That
advice is about **label vocabulary** — three other first-party sources instruct
the opposite for output shape generally. We say nothing about the label
vocabulary (Choices owns it) and are maximally prescriptive about the JSON.

### Payload shape

```json
{"v":1,"j":"signal-density","trig":true,"inj":false,"bl":null,
 "chk":[{"id":"C1","ok":true,"sp":"<verbatim span>"}],
 "ev":["<verbatim span>"],"why":"<reasoning>","conf":"high","x":{}}
```

- **Short keys** cut ~15% of payload tokens.
- **No key named `label` or `explanation`.** If a model ever falls back to the
  no-tool-calling path, AX parses free text with a regex that searches for
  `label` and truncates at `explanation`; a payload containing those keys gets
  shredded.
- **`score` is deliberately absent.** The label is the score. Two sources of
  truth is how a suite silently disagrees with itself.
- **`bl` is new**, and is the best thing this port buys you — see below.

### `bl` is a genuine gain

The parent schema has no field naming *which* bright line fired, so the 0-vs-0.5
decision is unauditable model arithmetic. With `bl` plus `chk`, the label can be
recomputed offline and compared against what the model chose. Disagreement means
the judge is not applying its own mapping rule — a judge-integrity monitor that
does not exist on the Anthropic path.

`verify.recompute_label` implements it; `export` reports it as
`label_divergence_rate`.

---

## Template, not code, evaluator

Code evaluators are disqualified **on secrets, not on network reach**.

`CustomCodeConfigRequest.__properties` = `[data_granularity, query_filter, type,
name, code, imports, variables, static_params]`. There is no `llm_config`, no
`ai_integration_id`, no env var, no secret store. The only way an
`ANTHROPIC_API_KEY` reaches that sandbox is hardcoded in `code` or as a string
`static_param` default — both stored in plaintext, versioned permanently in the
Eval Hub, and readable by any space member via `ax evaluators get-version`. A
live production credential in an observability config store is not a trade worth
making, and whether the sandbox even has egress becomes irrelevant once that is
true.

Template evaluators are the only path with a first-class
`llm_config.ai_integration_id`, and Anthropic is a supported evaluator provider.

**One code evaluator is worth keeping in reserve** (Enterprise only): a
deterministic re-scoring pass that reads `eval.<judge>.explanation`, parses the
payload and recomputes the label in Python. It needs no key and no network, so
the secrets objection does not apply. Treat it as an upgrade — `export` already
does the same job outside Arize.

---

## Naming: the trap that silently drops data

```
TemplateConfigInput.name   allows  ^[a-zA-Z0-9_\s\-&()]+$    (hyphens OK)
_EVAL_NAME_REGEX           allows  [a-zA-Z0-9_\s]+?          (no hyphens)
```

Arize will happily accept an evaluator named `capability-honesty-traced` and
produce `eval.capability-honesty-traced.score` — which the SDK's own column
pattern then fails to match, dropping it with an **INFO log rather than an
error**. Every judge id in this suite is hyphenated.

`naming.to_ax_name` normalises to snake_case, legal on both sides. The cost is
that Arize column names no longer match filenames; `to_judge_id` inverts it.

---

## Idempotence without a declarative surface

There is no Terraform provider, no `ax apply -f suite.yaml`, no YAML/JSON import.
"Config as code" here means a reconcile script you own.

`provision` hashes each rendered template. Unchanged hash → no-op. Changed hash →
a new evaluator **version** with the git SHA as its commit message. Ids land in
`evaluators.lock.json`, which is the manifest CI reconciles against.

Version pinning is mandatory in `task_plan`: a null `evaluator_version_id` means
"always latest", so any Eval Hub edit silently changes what production runs
mid-window with no version boundary in the data.
