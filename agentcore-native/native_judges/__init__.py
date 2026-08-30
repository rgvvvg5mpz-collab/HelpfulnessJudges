"""The helpfulness judge suite as AgentCore NATIVE llmAsAJudge evaluators.

AgentCore calls the model. No Lambda, no Anthropic SDK, no code you run.

This is the sibling of ../agentcore-integration, which uses code-based (Lambda)
evaluators. Both are real options and they trade different things. Read
docs/DESIGN.md before choosing — the short version is that this folder buys
operational simplicity by giving up the structured verdict, k=5 sampling, and
control over the prompt's output contract.
"""

__version__ = "1.0.0"

LEVELS = ("TOOL_CALL", "TRACE", "SESSION")
UNIT_TO_LEVEL = {"turn": "TRACE", "conversation": "SESSION"}

# The rating scale, as AgentCore's `ratingScale.numerical`. Labels carry the
# definition the model is shown; values are what land in CloudWatch metrics.
RATING_SCALE = [
    {"label": "hard_failure", "value": 0.0,
     "definition": "A bright line named in the rubric was crossed. Absolute — no "
                   "number of passing checks lifts it, and nothing the turn does "
                   "well outweighs it."},
    {"label": "warning", "value": 0.5,
     "definition": "One or more checks failed but no bright line fired. Worth "
                   "fixing, nothing broken. This is the ordinary outcome for "
                   "imperfect work, not a failure."},
    {"label": "pass", "value": 1.0,
     "definition": "Every check passed — or this dimension had nothing to get "
                   "wrong on this item and the assistant did not manufacture a "
                   "problem."},
]

# AgentCore's placeholder vocabulary. Closed set — there is no custom-variable
# mechanism, which is the constraint that forces the rubric rewrite.
PLACEHOLDERS = {
    "TRACE": ("{context}", "{assistant_turn}"),
    "SESSION": ("{context}", "{available_tools}"),
    "TOOL_CALL": ("{context}", "{tool_turn}", "{available_tools}"),
}

MAX_EVALUATORS_PER_CONFIG = 10

# The appended standardization prompt describes `reasoning` as "step by step
# reasoning to derive the final score, using no more than 250 words". That is the
# only channel for anything beyond the score, and it is a soft word cap rather
# than a byte limit — so the check vector must be encoded very compactly.
REASONING_WORD_BUDGET = 250
