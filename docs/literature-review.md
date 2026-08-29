# Literature review

Grounding for a prompt-based LLM-judge suite scoring helpfulness-domain
qualities in Vanguard's customer-facing chatbots.

> **v2 note.** The suite is ten ungated judges on a 0 / 0.5 / 1.0 scale. §2
> (binary checks) and §8 (reliability) most directly justify that scale. §6 (CX
> and service science) now carries `conduct-adaptation` and `capability-honesty`,
> which were re-grounded out of §7 when the compliance judges were dropped. §7 is
> retained as a record of descoped coverage.

This review was assembled from six parallel web-research sweeps covering judge
methodology, empathy and emotional-intelligence measurement, helpfulness and
verbosity attributes, regulated financial-services constraints, customer-
experience science, and judge reliability engineering. About 145 works were
surfaced; the ones below are those that changed a design decision.

**A note on citation confidence.** Each sweep was asked to mark whether it had
actually seen a work in search results or fetched it, versus recalled it. Works
below are marked `[verified]` where the sweep confirmed title and content from a
retrieved page, and `[unverified]` where it was recalled or seen only
second-hand. Several 2026 arXiv identifiers appear; these are recent and should
be spot-checked before the review is cited externally. **Verify every citation
before this document leaves the team.**

---

## 1. Why one judge per construct, and never an omnibus score

The single most consequential architectural finding.

**FLASK** (Ye et al., ICLR 2024, arXiv:2307.10928) `[verified]` decomposes a
coarse quality score into twelve skills across four groups — Logical
Correctness, Robustness and Efficiency; Factuality and Commonsense;
Comprehension, Insightfulness, Completeness and Metacognition; Readability,
Conciseness and Harmlessness. Skill-specific evaluation raised human–model
Spearman correlation from **0.641 to 0.680** against the same annotations. Four
of those twelve map almost one-to-one onto this suite's targets.

**Prometheus** (Kim et al., ICLR 2024, arXiv:2310.08491) and **Prometheus 2**
(arXiv:2405.01535) `[verified]` establish the rubric contract that follows from
this: four inputs — the instruction, the response, a reference answer that would
score 5, and a rubric consisting of a criterion description *plus an explicit
written description for each of scores 1 through 5*. Prometheus-13B reached
Pearson **0.897** with human evaluators across 45 customized rubrics, on par with
GPT-4 (0.882) and far above ChatGPT (0.392). Prometheus 2 further shows absolute
and relative grading are different skills that should not share a prompt.

A 2026 survey of rubric design (Chen et al., arXiv:2606.08625) `[unverified]`
finds analytic and atomic rubrics beat holistic ones because they make scoring
auditable — and, importantly, that **excessive rubric complexity reduces
human–LLM consistency**. Irrelevant criteria inject noise. The practical target
is 5–8 checks per construct; a check that never changes a score on the
validation set should be deleted.

> **Applied here:** twelve separate prompts, each with one criterion, 5–7 binary
> checks, and a written description of every score level. See
> [methodology.md](methodology.md).

## 2. Binary checks beat Likert judgment on subjective constructs

**CheckEval** (Lee et al., EMNLP 2025, arXiv:2403.18771) `[verified]` reports a
**+0.45 gain in average cross-evaluator agreement** and reduced score variance
from replacing subjective Likert judgment with decomposed yes/no questions. Its
diagnosis names the exact problem this suite faces: *subjective criteria plus a
Likert scale* is the root cause of judge inconsistency — which is to say,
empathy, tone, and personalization.

**HealthBench** (OpenAI, 2025) `[verified]` is the largest deployed instance of
the same pattern: 48,562 physician-written criteria, each graded binary
met/not-met with importance weights. **TICK** (Cook et al., arXiv:2410.03608)
`[verified]` shows generated checklists improve both evaluation and generation.
**InFoBench** (Qin et al., arXiv:2401.03601) `[verified]` contributes DRFR —
decomposed requirement following — as the metric shape for coverage.

**Scale granularity.** Li et al. (arXiv:2601.03444) `[unverified]` compared 0–5,
0–10, 0–100, and binary and found **0–5 gives the highest ICC alignment with
humans**. This is why the suite uses 1–5 rather than MT-Bench's 1–10.
Stureborg et al. (arXiv:2405.01724) `[verified]` document the failure mode of an
unanchored scale: score compression, with everything landing on 4 and the judge
detecting nothing. Related work on scoring bias (arXiv:2506.22316) `[verified]`
finds the same central-tendency collapse and identifies calibration exemplars
plus narrow scales as the mitigation.

## 3. The bias inventory, in order of how much it will hurt

**Style and formatting bias is the dominant bias — larger than position bias.**
A 2026 systematic evaluation of mitigation strategies (arXiv:2604.23178)
`[unverified]` reports four of five judges showing near-unanimous markdown
preference on content-equivalent pairs, where humans preferred markdown only
0.57 of the time with 0.53 annotator agreement. **SOS-Bench** (Feuer et al.,
ICLR 2025, arXiv:2409.15268) `[verified]` reaches the same conclusion from a
different direction. This is the single most dangerous bias for a suite that
scores clarity and completeness.

**Verbosity/length bias.** Zheng et al.'s "repetitive list" attack (MT-Bench,
arXiv:2306.05685) `[verified]` fooled GPT-3.5 and Claude-v1 **91.3%** of the
time and GPT-4 8.7% of the time. Length-controlled AlpacaEval (Dubois et al.,
arXiv:2404.04475) `[verified]` shows length bias survives instruction and needs
statistical control, not just a prompt sentence. The RLHF-side literature — *A
Long Way To Go* (Singhal et al., arXiv:2310.03716), *Disentangling Length from
Quality in DPO* (Park et al., arXiv:2403.19159), **ODIN** (Chen et al., ICML
2024, arXiv:2402.07319) `[all verified]` — establishes that length correlation is
a property of the reward signal itself, which is precisely what a judge suite
becomes once anyone optimizes against it.

**Position bias.** Zheng et al. measure 10–15 points. Consistency after swapping
A/B with the default prompt: GPT-4 65.0%, GPT-3.5 46.2%, Claude-v1 23.8% — and
Claude also showed a *name* bias, moving from 23.8% to 56.2% when the assistants
were renamed. Shi et al. (IJCNLP 2025, arXiv:2406.07791) `[verified]` find
position bias is worst when candidates are close in quality, which is exactly
the prompt-A/B-testing case. **This is a decisive argument for pointwise
scoring** as the production default: Tripathi et al. (arXiv:2504.14716)
`[verified]` found pairwise preferences flip ~35% under distractor manipulation
versus ~9% for absolute scores.

**Self-preference.** Panickssery et al. (NeurIPS 2024, arXiv:2404.13076)
`[verified]` show a linear relationship between self-recognition and
self-preference. Zheng et al. measured GPT-4 giving itself ~10% and Claude-v1
~25% higher win rates than humans did. G-Eval's authors flag general bias toward
LLM-generated text. **Use a different model family for the judge than for the
chatbot.**

The 2026 mitigation study's most useful negative result: **style-blind
instructions alone have minimal effect.** The four MT-Bench debiasing sentences
are necessary and nowhere near sufficient. Layer them with rubrics written as
comprehension outcomes, format-normalized calibration examples, logged token
counts, and periodic perturbation audits.

## 4. Empathy: what is actually measurable in text

**EPITOME** (Sharma et al., EMNLP 2020, arXiv:2009.08441) `[verified]` is the
foundation: three mechanisms — **Emotional Reactions**, **Interpretations**,
**Explorations** — each coded 0 (absent) / 1 (weak) / 2 (strong). Explorations
were associated with 47% more replies in the corpus.

The critical follow-up is **Kumar et al. 2025** (arXiv:2506.10150, *Nature*)
`[verified]`, which measured how reliably *human experts* annotate each
sub-component: Explorations reached weighted kappa ~0.69–0.76, but
**Interpretations reached only ~0.29**. This is a hard ceiling. A judge cannot
be held to an agreement target above the human ceiling for the same construct —
so Interpretations must be operationalized as *observable restatement and
inference* rather than as "did the assistant understand," and its target
agreement must be set from Vanguard's own SME-vs-SME measurement.

**HEART** (arXiv:2601.19922) `[unverified]` supplies the cleanest published
definition of the real/performative discriminator — whether the response "tracks
the specific details and emotional signals in the context rather than offering
generic reassurance" — and, crucially, finds that **LLM judges over-reward
generic reassurance and polished fluency**. That finding is written directly
into `empathic-attunement` as an instruction to the judge.

**Escalation is independent of support.** Chi et al. (arXiv:2605.21569)
`[unverified]` find responses can be simultaneously supportive and escalating,
and that "friend"-style personas raised both regulation *and* escalation while
measured professional personas reduced escalation while preserving support. This
dissociation is why `emotional-accuracy` scores non-escalation separately from
`empathic-attunement`'s warmth-specificity.

**Emotion identification is separable from warmth.** **EmoBench** (Sabour et
al., ACL 2024) `[verified]` separates Emotional Understanding (emotion + cause)
from Emotional Application. **SECEU** (Wang et al., 2023) `[verified]` uses a
point-allocation design over four emotions per scenario, which is the right
model for handling ambivalence. Warm-and-wrong is worse than neutral-and-correct,
and only separate scores expose it.

**Supporting instruments.** **ESConv** (Liu et al., ACL 2021, arXiv:2106.01144)
`[verified]` supplies the eight-strategy taxonomy and the canonical misuse —
leading with Providing Suggestions on a turn carrying a fresh emotional
disclosure. The **CARE Measure** (Mercer et al. 2004) `[verified]` supplies the
fusion of relational and instrumental help, and the precedent for an explicit
"Does Not Apply" response category. **MITI 4.2.1** `[unverified]` supplies the
simple-versus-complex-reflection line, which is the same distinction as weak
versus strong Interpretation.

**Two cautions.** Ayers et al. (JAMA Internal Medicine, 2023) `[verified]` found
chatbot responses rated more empathetic than physicians'; but a 2025 *Nature
Human Behaviour* study `[verified]` finds empathy is **devalued when disclosed as
AI-generated**. Cuadra et al. (CHI 2024) `[unverified]` characterize the failure
as "flippant and hollow." Together these say: emotion-word inflation from a
disclosed AI reads as performative. Cap it rather than reward it.

**Applicability is a construct in its own right.** An Empathy Applicability
framework (arXiv:2601.09696) `[unverified]` assesses whether empathic content is
appropriate *before* scoring it, yielding N/A rather than a low score. This is
what protects a completeness judge from being gamed by emotional padding, and it
is why every relational judge in this suite is gated.

## 5. Non-terseness without buying padding

This is the hardest design problem in the suite, because the naive fix — reward
completeness — is exactly what length bias exploits.

**HelpSteer2** (Wang et al., arXiv:2406.08673) and **HelpSteer** (arXiv:
2311.09528) `[both verified]` supply the five-attribute model — helpfulness,
correctness, coherence, complexity, **verbosity** — with verbosity treated as a
**descriptive 0–4 axis with a target band**, not a monotone good. That framing is
the key move: *the right amount of detail depends on what the prompt calls for.*

**Verbosity ≠ Veracity** (Zhang et al., arXiv:2411.07858, ACL 2025 workshop)
`[verified]` supplies the operational definition that makes the anti-padding
judge possible: a response is verbose if it "can be further compressed with fewer
tokens while keeping the same meaning." It also supplies a five-type padding
taxonomy — restating the question; enumerating candidates to avoid committing;
imprecision dressed as thoroughness; detail beyond the ask; verbose formatting.
`signal-density` names all five.

**InstructGPT's labeling instructions** (Ouyang et al., arXiv:2203.02155
Appendix B) `[verified, retrieved]` are worth quoting directly for the same
reason — "not giving overly long or rambling answers, or repeating information
from the question" — and §4.3 documents over-hedging as an artifact of rewarding
epistemic humility.

**The over-refusal literature** — **XSTest** (Röttger et al., NAACL 2024,
arXiv:2308.01263) and **OR-Bench** (Cui et al.) `[both verified]` — establishes
that unhelpful non-engagement is measurable and common, with contrastive
should-answer / should-decline pairs as the required test design. An early
GPT-3.5 release rejected 57% of the OR-Bench hard set. In a regulated domain the
temptation to treat refusal as the safe default is strong; the literature says
it is a measured failure with its own rate.

**HealthBench's hedging axis** `[verified]` supplies the three-case structure
this suite adopts wholesale in `calibrated-hedging`: reducible uncertainty (ask
for the fact), irreducible (hedge, don't interrogate), none (answer plainly).

> **Applied here:** `need-coverage` scores coverage of an *enumerated
> requirement set* — length-independent by construction — and `signal-density`
> scores compressibility. They are an opposed pair and are never traded off
> inside one judge.

## 6. What makes a service interaction feel helpful

**Effort, not delight.** Dixon, Freeman & Toman (HBR 2010) and *The Effortless
Experience* (2013) `[both verified]` establish effort reduction as the dominant
driver of loyalty. Gartner (2024) `[verified]` reports only 14% of customer
service issues are fully resolved in self-service, and that most channel
switches are effortful. Next-Issue Avoidance — pre-empting the predictable
adjacent problem — is the highest-leverage move available to a single turn.

**Concreteness, not warmth.** Packard & Berger (*Journal of Consumer Research*,
2021) `[verified]` find linguistic concreteness raises satisfaction — specific
nouns, actual amounts, concrete verbs. Luo et al. (*Marketing Science*, 2019)
`[verified]` find bot emotional language is discounted a priori once AI identity
is known. The judge instruction that follows is blunt: emotional adjectives do
not raise a listening score; specificity does.

**Repair is the highest-leverage turn.** Ashktorab et al. (CHI 2019) `[verified]`
rank repair strategies after a chatbot misparse; offering concrete
interpretations to choose from beats asking the user to rephrase. Dietvorst,
Simmons & Massey (2015, 2018) `[verified]` explain why it matters more than for a
human agent — algorithm aversion means confidence collapses faster after an
identical error, and restoring the user's control is the documented repair.

**Accommodation is two-sided too.** The personalization–privacy and
anthropomorphism literature, plus Luo et al.'s finding that disclosed bot empathy
is discounted in advance, together make the case that *over*-accommodation is a
real defect rather than harmless excess: an assistant that slows down and
simplifies at a customer who was managing fine costs them time and reads as
condescension. This is what lets `conduct-adaptation` be scored ungated and
two-sided, which its regulatory predecessor could not be — a supervisory
expectation describes only the failure to accommodate, never the excess.

**Narrated work raises perceived value, which is why fabricating it attacks the
metric.** Buell & Norton (*Management Science*, 2011) `[verified]` establish the
labor illusion: showing the work raises satisfaction independently of the outcome.
Read against an eval suite rather than a service desk, that finding is a warning —
an assistant can inflate coverage, actionability and attunement scores at once by
claiming work it never did, and only a judge with access to the tool trace can
tell. This is the entire grounding for `capability-honesty`, and it is a
measurement-integrity argument rather than a disclosure one.

**Escalation is two-sided error.** Liu et al. (AAAI 2021) `[verified]` formalize
machine–human chatting handoff with a Golden Transfer within Tolerance metric:
escalating too late *and* too early are both failures. Premature escalation
manufactures effort.

**The CFPB Issue Spotlight** (June 2023) `[verified]` is the most directly
applicable document in this whole review. It names the **doom loop** — the
customer asks substantially the same question again and the bot does not change
strategy — and the **dispute-regurgitation** pattern, where the bot restates the
fee or policy the customer is explicitly disputing as though it were an answer.
Both are hard failures in `effort-and-resolution-path` and `need-coverage`.

**Waits.** Maister (1985) `[verified]` — unexplained waits feel longer;
unbounded waits feel longest. The full pattern is window + reason + confirmation
signal.

**Task-oriented dialogue benchmarks** — MultiWOZ Inform/Success, ABCD (Chen et
al., NAACL 2021), τ-bench (Yao et al., arXiv:2406.12045), τ²-bench
(arXiv:2506.07982), ALMITA, JourneyBench (EACL 2026) `[all verified]` — supply
the coverage-of-required-nodes framing used in `need-coverage`.

## 7. The regulatory boundary conditions

> **Descoped in v2.** The four judges this section grounded were removed at
> request. Two of them returned re-grounded in §6 material —
> `conduct-adaptation` and `capability-honesty` — with every rule below stripped
> out; nothing in the current ten judges enforces any of them. The section is kept
> because it documents what the suite no longer covers, and because
> `plain-language-clarity` still draws its readability standard from here.


These do not create separate compliance judges. They create **caps inside the
helpfulness judges**, because the failure mode is that maximizing warmth or
specificity crosses a line.

**FINRA Rule 2210(d)** `[verified, rulebook fetched]` is the operative text.
(d)(1)(A) requires fair and balanced treatment and prohibits material omission;
(d)(1)(B) prohibits false, exaggerated, unwarranted, **promissory** or misleading
claims; (d)(1)(F) prohibits **predictions or projections of performance**;
(d)(1)(E) requires audience-appropriateness. The finding that drove
`reassurance-integrity` into existence: *the false-reassurance failure usually
appears in the response's warmest sentence.* "Markets always recover, you'll be
fine" is a 2210(d)(1)(F) projection and a (d)(1)(B) promissory statement, and
every other judge in a naive helpfulness suite rewards it.

**SEC Regulation Best Interest** (2019 adopting release) `[verified]` supplies
the "call to action" and degree-of-tailoring tests that define the
education/recommendation line. **DOL Interpretive Bulletin 96-1** (29 CFR
2509.96-1) `[verified]` supplies the participant-education safe harbors and the
requirement that naming specific plan options be accompanied by reasonably
available alternatives and material assumptions. **FINRA Rule 2111** `[verified]`
extends suitability to **hold** recommendations — telling a worried customer to
stay put is a recommendation.

**Vulnerable and senior investors.** FINRA **Rule 2165** `[verified]` defines a
"specified adult" as 65+ or any adult who appears unable to protect their own
interests. **Regulatory Notice 07-43** `[verified, fetched]` enumerates the red
flags: sudden atypical or unexplained withdrawals, drastic shifts in investment
style, third-party pressure, isolation from family, a new acquaintance directing
funds, urgency combined with secrecy. **Rule 4512** and **RN 22-31** `[verified]`
cover trusted contacts — a resource for the firm, not an authority over the
account. The **FCA's FG21/1** `[verified]` and its March 2025 review supply the
four drivers of vulnerability and the supervisory expectation that firms
recognize and respond. These make non-acknowledgment a score cap, not a
deduction.

**Complaints.** FINRA **Rule 4513** and **4530(d)** `[verified]` define a
customer complaint broadly — any grievance involving the firm's handling of the
customer's securities or funds — and require records and quarterly reporting.
A chatbot that absorbs a grievance sympathetically and moves on has created a
records problem, not just a service problem.

**Plain English.** SEC Rule 421(d) and *A Plain English Handbook* (1998)
`[verified]` supply the six principles. The trap the review names explicitly:
**clarity purchased by dropping a material qualification is a 2210(d)(1)(A)
failure, not a clarity win.** This is why `plain-language-clarity` scores
readability and material-omission jointly on one dimension rather than letting
them trade off.

**AI-specific.** FINRA **RN 24-09** on generative AI, the FINRA 2025 and 2026
Regulatory Oversight Reports (the latter defining hallucination and naming PII
in prompts as a data-leakage risk), SEC Division of Examinations 2025 and 2026
priorities (accuracy of AI representations; **AI-washing** as an antifraud
matter), the **EU AI Act Art. 50** transparency obligations (applicable from
2 Aug 2026), the **Utah AI Policy Act**, and **NIST AI 600-1** (Confabulation;
Human-AI Configuration; anthropomorphism and emotional entanglement)
`[verified, except NIST and Utah: unverified]`. Together these ground
`capability-honesty`.

> **Assumption, flagged.** Nothing here was verified against Vanguard's own
> policies, supervisory procedures, or approved-language library. Every
> regulatory anchor in these rubrics is a *published-rule* anchor and must be
> re-derived from Vanguard's actual policy text before launch — see PReMISE
> (arXiv:2605.30803) `[unverified]` for the atomic-decomposition pipeline.

## 8. Making the suite reliable enough to trust

**Judges are noisier than they look.** *The Coin Flip Judge* (arXiv:2606.13685)
`[unverified]` measures a **13.6% average pairwise verdict flip rate** across
repeated runs on identical input, with 28% of questions above a 20% flip rate,
and estimates ~11 trials needed to recover a stable 50-trial verdict at 95%
probability. *Rating Roulette* (arXiv:2510.27106) `[unverified]` reports the same
self-inconsistency phenomenon. Temperature 0 is not reproducibility. **Sample k
times and aggregate; log the spread as a per-item confidence signal.**

**Report chance-corrected agreement.** Thakur et al. (arXiv:2406.12624)
`[verified]` show raw percent agreement flatters judges badly on skewed
distributions and that even large judges sit well behind inter-human agreement.
Use ordinal-weighted Krippendorff's alpha; α ≥ 0.80 to draw conclusions, 0.667 as
a tentative floor — **but set the real bar relative to your own SME-vs-SME
ceiling per dimension.** Bavaresco et al. (JUDGE-BENCH, ACL 2025,
arXiv:2406.18403) `[verified]` show agreement varies sharply by the property
judged and is consistently higher against non-expert than expert labels.

**Validate substitutability statistically.** Calderon, Reichart & Dror's
**alt-test** (ACL 2025, arXiv:2501.xxxxx) `[verified]` compares the judge against
the *distribution* of human annotations rather than expecting it to replicate any
single annotator — the right framing where human raters genuinely disagree, which
is empathy and tone.

**Panels beat single judges — but agreement is not correctness.** **PoLL**
(Verga et al., Cohere, arXiv:2404.18796) `[verified]` shows three smaller models
from *disjoint* families beat a single GPT-4 judge with less intra-model bias at
over 7× lower cost. The counterweight: *Reliability without Validity*
(arXiv:2606.19544) `[unverified]` documents judges that are internally consistent
and highly correlated with each other while jointly diverging from humans. The
acceptance gate is always judge-vs-SME.

**Abstention should be a first-class output.** *Trust or Escalate* (Chaudhary et
al., ICLR 2025, arXiv:2407.18370) `[verified]` provides selective evaluation with
provable human-agreement guarantees on the covered subset, and shows naive
predictive-probability confidence is brittle. This is what licenses a defensible
claim of the form: *"on the 88% of conversations where the Empathy judge does not
abstain, agreement with our SMEs is ≥ 0.75 α at 95% confidence."*

**Never report a raw judge average.** *How to Correctly Report LLM-as-a-Judge
Evaluations* (ICML 2026, arXiv:2511.21140) and **StratPPI** (Fisch et al.,
NeurIPS 2024, arXiv:2406.04291) `[verified]` give prediction-powered inference:
judge scores plus a small human-labeled calibration set yield an unbiased
estimate with a valid confidence interval that includes calibration uncertainty,
and stays unbiased under distribution shift.

**Injection is a live threat, and filters do not work.** **JudgeDeceiver** (Shi
et al., ACM CCS 2024, arXiv:2403.17710) `[verified]` explicitly evaluated
known-answer detection, perplexity detection, and windowed perplexity detection
and found all insufficient. A companion study (arXiv:2505.13348) `[verified]`
measures >30% attack success for comparative-undermining attacks and identifies a
separate **justification-manipulation attack** that poisons the rationale while
the score looks correct — which matters enormously if rationales reach compliance
reports. Structural defenses are required: delimiters, data-not-instructions
framing, schema-constrained output, and evidence-span verification against the
transcript.

**Attribute drift.** *Who Drifted: the System or the Judge?* (arXiv:2606.15474)
`[unverified]` frames the production ambiguity precisely — a score drop is
uninterpretable unless you can attribute it — and proposes a frozen human-labeled
anchor set monitored with anytime-valid e-processes so daily dashboard peeking
does not inflate false alarms.

**Rubrics are code.** *The Coin Flip Judge* finds semantically equivalent prompt
templates flip majority outcomes in **25%** of cases. Shankar et al. (UIST 2024)
`[verified]` name "criteria drift" — grading outputs is how people discover their
criteria, so some criteria are only definable after seeing real transcripts.
Version-pin rubrics, re-run the anchor set on every edit, and never change a
rubric and a judge model in the same release.

**Cost.** Cost-aware routing work (arXiv:2602.15481; RACER, arXiv:2605.10805)
`[unverified]` shows cheap-first cascades retaining 97–99% of the strongest
judge's accuracy at large cost reductions. **FLAMe** (Vu et al., EMNLP 2024,
arXiv:2407.10817) `[verified]` shows purpose-trained autoraters beating GPT-4 and
Claude-3 on 8 of 12 benchmarks — the distillation endgame, which for Vanguard
also answers the data-residency question.

**Governance framing** (descoped with §7, retained for reference). The revised interagency model-risk guidance
(**SR 26-2 / OCC Bulletin 2026-13**, April 2026, replacing SR 11-7)
`[verified]` keeps three validation pillars — conceptual soundness, ongoing
monitoring, outcomes analysis — which the validation protocol maps to directly.
Commentary reads the guidance as placing generative and agentic AI outside its
explicit scope while expecting existing risk practices to apply; treat it as the
organizing frame and confirm scope with Vanguard's model risk function.

---

## Open questions this review could not close

1. **What agreement is achievable on Empathy and Tone for retail-investor
   conversations?** No published judge-vs-expert figures exist for
   financial-services empathy rubrics. The SME-vs-SME ceiling must be measured
   before any target is set — it may be low enough that these judges are only
   ever directional.
2. **Are eight dimensions statistically separable, or do they collapse into two
   or three latent factors?** A factor analysis on the gold set would settle it
   and could substantially reduce judge cost. This is the single highest-value
   analysis to run first.
3. **Does checklist decomposition preserve the construct for genuinely gestalt
   qualities?** A response can tick every empathy box and still read as cold. No
   work tests whether decomposition loses construct validity precisely on the
   affective dimensions where it is most attractive for reliability.
4. **Does panel diversity survive model convergence?** PoLL's finding depends on
   genuinely decorrelated errors across families. Frontier models have converged
   substantially since 2024. Test on your own gold set; do not assume.
5. **What is the residual injection attack-success rate after hardening?**
   Published figures are for unhardened judges. Nobody has measured ASR with
   delimiting, data-not-instructions framing, schema constraint, and
   evidence-span verification all applied.
6. **Do the judges predict outcomes?** SR 26-2's outcomes-analysis pillar implies
   tying judge scores to repeat-contact rate, escalation rate, CSAT, and task
   completion. That is the real construct-validity test and is entirely separate
   from SME agreement.
7. **How many trials k for pointwise ordinal scoring?** The ~11-trial figure is
   for pairwise verdicts. Pointwise scores with checklist decomposition may
   stabilize much faster, but no published reliability curve covers that setting.
