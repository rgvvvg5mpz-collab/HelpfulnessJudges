"""Run the helpfulness judge suite on Amazon Bedrock AgentCore, over CloudWatch traces.

Shape: each judge is a native AgentCore **code-based Evaluator** backed by one
Lambda. AgentCore owns selection, sampling, cadence and write-back; the Lambda
owns the judging, so the existing Anthropic pipeline runs unchanged — json_schema
output, effort, prompt caching, k=5 and the median all survive.

Results land as `gen_ai.evaluation.result` OpenTelemetry events parented to the
evaluated span, in CloudWatch, with metrics auto-extracted to the
`Bedrock-AgentCore/Evaluations` namespace.

Standalone: does not import from the parent repository. Rubrics are vendored
under `rubrics/` with hashes — and, unlike the Arize integration, **none of them
is modified**.
"""

__version__ = "1.0.0"

# AgentCore evaluation levels, verified from the botocore service model:
#   bedrock-agentcore-control :: CreateEvaluator :: level enum
LEVELS = ("TOOL_CALL", "TRACE", "SESSION")

# unit in the rubric frontmatter -> AgentCore level.
#
# `capability-honesty-traced` deliberately maps to TRACE, not TOOL_CALL. A
# TOOL_CALL evaluator fires once per call and sees only the calls PRECEDING the
# one under evaluation, which cannot support the judge's `unreported_work`
# direction — the whole point is spotting work the turn never reported.
UNIT_TO_LEVEL = {"turn": "TRACE", "conversation": "SESSION"}

# A code-based evaluator returns exactly these three. Everything else the judge
# produces is serialised into `explanation`. `boundary.agentcore_result` builds
# that dict and the suite asserts its keys against this tuple, so the contract is
# named once rather than being an unwritten property of one return statement.
RESULT_FIELDS = ("label", "value", "explanation")

# The scale, and the single definition of it. Every other spelling in this
# package derives from these four names: the label AgentCore is handed, the label
# `verify` accepts, the enum Anthropic enforces at generation time, and the bands
# the offline scorer balances its draw across. They were previously written out
# by hand in six places, which is five opportunities for the table to disagree
# with the schema about what a score even is.
SCALE = {"1.0": 1.0, "0.5": 0.5, "0": 0.0}          # label -> value
LABELS = tuple(SCALE)                                # ("1.0", "0.5", "0")
LABEL_OF = {v: k for k, v in SCALE.items()}          # value -> label

# Lowest band first: the remainder of an uneven `score --limit` goes here in
# order, so `--limit 7` buys the extra sample for the hard failures rather than
# for the passes.
BANDS = tuple(sorted(SCALE.values()))                # (0.0, 0.5, 1.0)

# Report columns, highest band first — the opposite order, because a reader scans
# a table best-to-worst. Derived from BANDS so the columns cannot drift from the
# scale they summarise.
BAND_COLUMNS = tuple(zip(reversed(BANDS), ("pass", "warn", "fail")))

# CreateOnlineEvaluationConfig accepts at most 10 evaluators. The suite fits with
# zero headroom, and only because variant mutual exclusion drops one side.
MAX_EVALUATORS_PER_CONFIG = 10
