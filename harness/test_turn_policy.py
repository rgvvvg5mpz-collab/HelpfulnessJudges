"""Sanity checks for turn selection. Run: .venv/bin/python -m harness.test_turn_policy"""
from harness.judge_spec import load_judge, load_judges
from harness.transcript import Turn, Conversation
from harness.turn_policy import turns_to_score

conv = Conversation(
    id="t",
    turns=[
        Turn("customer", "How do I change my address?"),
        Turn("assistant", "Under Profile settings."),
        Turn("customer", "Thanks. Also I'm really worried -- I was laid off and my balance dropped."),
        Turn("assistant", "That sounds hard. Here are your rollover options."),
        Turn("customer", "Which form do I need?"),
        Turn("assistant", "Form 5305-R."),
    ],
)
flat = Conversation(
    id="f",
    turns=[
        Turn("customer", "What is the 2026 contribution limit?"),
        Turn("assistant", "$24,500."),
    ],
)

emp = load_judge("empathic-attunement")
cov = load_judge("need-coverage")
eff = load_judge("effort-and-resolution-path")

cases = {
    "empathy/all -- every assistant turn, no affect filter": turns_to_score(emp, conv, "all"),
    "coverage/all -- identical, judges are independent":     turns_to_score(cov, conv, "all"),
    "empathy/last -- sampling mode":                          turns_to_score(emp, conv, "last"),
    "empathy on single-exchange conversation":                turns_to_score(emp, flat, "all"),
    "effort (conversation unit) -- always once":              turns_to_score(eff, conv, "all"),
    "effort ignores mode":                                    turns_to_score(eff, conv, "last"),
}
for k, v in cases.items():
    print(f"{k:56} {v}")

# Independence is the property under test: no judge sees a different turn set
# from any other judge of the same unit. A divergence here means a gate crept back
# in. The judge list is derived from the suite, never hardcoded — a hardcoded list
# silently stops covering any judge added after it was written.
all_judges = load_judges()
turn_judges = [j for j in all_judges if j.unit == "turn"]
conv_judges = [j for j in all_judges if j.unit == "conversation"]
assert turn_judges and conv_judges, "suite must have judges of both units"

sets = {j.id: turns_to_score(j, conv, "all") for j in turn_judges}
assert len(set(map(tuple, sets.values()))) == 1, f"turn judges disagree on scope: {sets}"
for j in conv_judges:
    for mode in ("all", "last"):
        assert turns_to_score(j, conv, mode) == [None], f"{j.id} must score once per conversation"
assert turns_to_score(emp, conv, "all") == [1, 3, 5]
assert turns_to_score(emp, conv, "last") == [5]
assert turns_to_score(emp, flat, "all") == [1]
assert turns_to_score(eff, conv, "all") == [None]
assert turns_to_score(eff, conv, "last") == [None]
print(f"\nall {len(turn_judges)} turn judges score an identical turn set -- no gating")
print(f"all {len(conv_judges)} conversation judges score once, in both modes")
print("all assertions passed")
