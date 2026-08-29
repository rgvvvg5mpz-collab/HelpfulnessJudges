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
# produces is serialised into `explanation`.
RESULT_FIELDS = ("label", "value", "explanation")

SCALE = {"1.0": 1.0, "0.5": 0.5, "0": 0.0}

# CreateOnlineEvaluationConfig accepts at most 10 evaluators. The suite fits with
# zero headroom, and only because variant mutual exclusion drops one side.
MAX_EVALUATORS_PER_CONFIG = 10
