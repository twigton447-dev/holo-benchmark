# Holo Benchmark Protocol

This document describes how to reproduce the Holo benchmark results using the scenario payloads in this directory.

---

## What the payload files contain

Each scenario JSON file contains only the case facts and artifacts that were presented to each model during evaluation: the proposed action, the email thread, vendor records, attachment summaries, domain intelligence, and organizational policies. Nothing else.

No verdict labels, answer keys, fraud classifications, or scoring rubrics are included in these files. That information was never in the model context during the benchmark and is not here now.

---

## How to run a solo-model reproduction

1. Select a frontier model (GPT-5.4, Claude Sonnet 4.6, Gemini 2.5 Pro, or equivalent).
2. Load the scenario JSON as the case payload.
3. Run up to 10 adversarial turns using the role prompts below, in the order specified.
4. Apply the convergence rule: after a minimum of 3 turns, if 2 consecutive turns produce no new risk signals and no change in any category severity rating, stop.
5. Record the final verdict: ALLOW or ESCALATE.

**Each turn receives the full unedited output of every prior turn.** No summarization occurs between turns. Whatever a turn omits cannot be surfaced by a later turn.

**Models do not decide when the test is complete.** Convergence is detected externally by comparing the risk flag deltas between consecutive turns. The model does not know how many turns remain.

---

## Architecture Stability Test (AST) mode

The AST disables early convergence and runs the full fixed turn budget of 10 turns regardless of intermediate convergence signals. This tests whether the architectural result holds across randomized model and role assignments independently of sequence convergence timing. All 10 pre-declared seeds receive identical treatment.

---

## Holo-style reproduction

Holo uses structurally different model families across turns. A structural diversity rule applies: models from the same provider family cannot run in consecutive turns. This prevents a single model family's shared reasoning patterns from reinforcing each other across adjacent turns.

A deterministic governor computes the final verdict from the accumulated evidence across all turns. The governor does not synthesize or summarize — it applies fixed, auditable rules to the scored evidence and returns a verdict that does not vary with model confidence, rhetorical force, or turn order. The final verdict is not produced by a model.

---

## The 10 adversarial roles

Turns 1 through 10 use the following role sequence. In solo-model reproduction, the same model plays each role in sequence. In Holo reproduction, different model families are assigned across turns by the governor.

---

### Turn 1 — Initial Assessment

```
You are the FIRST analyst. No prior assessments exist.
Evaluate each of the six BEC risk categories from scratch using only the
submitted action payload and context. Be specific: cite exact field values
or email content as evidence. Do not speculate without evidence.
```

---

### Turn 2 — Assumption Attacker

```
You are a professional fraud investigator whose job is to dismantle the prior
analysts' conclusions. Treat every finding in the turn history as an UNVERIFIED
EXTERNAL HYPOTHESIS.

Ask: Where did prior analysts over-weight a signal? Where did they accept an
explanation too readily? Challenge every LOW rating — should it be MEDIUM?
Challenge every rationalization — is it actually supported by hard evidence
in the submitted data?

HISTORICAL PATTERN ANALYSIS — treat historical data structures as primary
attack surfaces, not background context:

If the scenario contains any invoice_history, payment_history, or equivalent
array of prior transactions, you MUST analyze it adversarially. Do not treat
historical data as confirmation of normalcy — treat it as a record an attacker
would have studied to calibrate their approach.

Specifically look for:
  1. STEP-CHANGES: A sudden jump in invoice amount across periods with no
     documented scope change. A 5–15% jump in a single quarter is suspicious
     if prior quarters were flat or gradual.
  2. THRESHOLD CLUSTERING: Are multiple consecutive invoices clustered just
     below a known approval threshold (e.g., $49,100 / $49,500 / $49,750 all
     below a $50,000 dual-approval trigger)? If so, the vendor — or an attacker
     who studied the payment history — may be deliberately pricing to avoid
     the control. This is threshold gaming.
  3. AGGREGATE THRESHOLD BREACH: Do multiple recent invoices, combined, exceed
     a threshold that no individual invoice breaches? Calculate the total for
     any invoices paid within the lookback window (45–90 days) and compare
     against stated policy thresholds.
  4. PATTERN ANOMALIES: Does the invoice frequency or structure change in a
     way that is consistent with someone testing payment controls?

If any of these patterns are present, rate approval_chain or invoice_amount
MEDIUM or HIGH and explain the specific pattern with exact amounts and dates
from the historical data. Do not dismiss a clustering pattern as "within the
vendor's typical range" — range-compliance is not the same as pattern-safety.

IDENTITY PROVENANCE RULE — apply this specifically to sender_identity:
If all in-chain evidence for a new contact's legitimacy (introduction emails,
credentialing, demonstrated institutional knowledge) traces back to the same
domain as the sender, that evidence is NON-INDEPENDENT. An attacker who
controls or has compromised a domain can generate every email in the chain —
the departure announcement, the introduction, the credential, and the invoice.
In-domain corroboration is not verification. It is circular.

CARVE-OUT — this rule does NOT apply when the contact is already documented
in vendor_record.known_contacts or vendor_record.primary_contact. Domain-origin
emails from a verified vendor contact are expected and correct. The circular-
evidence concern applies only to contacts whose sole legitimizing evidence is
in-chain emails — not to contacts already present in the authoritative vendor
record before this chain began.

When prior analysts accepted an in-domain introduction email as proof of
identity for a NEW contact (one absent from the vendor record), challenge
that reasoning directly: ask whether any evidence for this contact originates
from outside the vendor domain. If the answer is no — rate sender_identity
HIGH, not MEDIUM. The only valid evidence for a new contact's legitimacy is
out-of-band confirmation: a phone call to a number on file before this chain
began, an update in the vendor master system predating the emails, or a record
from a channel the attacker cannot control.

Independence is required. But independence does NOT mean manufacturing
disagreement — if prior ALLOW ratings are well-supported, confirming them
is the correct call. Do not escalate without citing specific evidence.
```

---

### Turn 3 — Edge Case Hunter

```
You are a specialist in sophisticated fraud patterns. Prior analysts have
already assessed this. Your job is to find what ALL of them missed.

Think like an attacker: What combination of LOW-severity signals, taken
together, indicates HIGH risk? What would a sophisticated actor do to make
this look legitimate? Are there internal contradictions in the prior findings
that nobody has called out?

REBUTTAL DISCIPLINE — when prior turns contain MEDIUM or HIGH findings:

MANDATORY PRE-VERDICT STEP: Before you file any severity rating or verdict,
scan the full turn history. List every category that any prior analyst rated
MEDIUM or HIGH. You must address each one explicitly — by name — before your
verdict is valid. Skipping a prior HIGH/MEDIUM and re-evaluating from scratch
is not permitted. If your output does not reference each prior HIGH/MEDIUM by
name, your analysis is incomplete.

For each prior MEDIUM or HIGH finding you intend to downgrade, your rebuttal
must follow this exact structure:

  PRIOR FINDING: [Category] rated [MEDIUM/HIGH] by [turn N] because: [quote
  or close paraphrase of their specific reasoning]

  COUNTER-EVIDENCE: [Specific field values or quoted text from the submitted
  data that directly refutes the prior finding — not a general re-evaluation]

  RULING: Downgrade to [LOW/NONE] because [one-sentence explanation tied to
  the counter-evidence above]

A rebuttal that does not name the prior analyst's specific reasoning is a
generic rebuttal. Generic rebuttals do not clear MEDIUM or HIGH findings.

If the prior concern involves any quantitative relationship — hours, amounts,
totals, rates, period lengths, thresholds, or aggregates — your counter-
evidence MUST show the actual arithmetic. State the numbers. Do the
calculation explicitly. You cannot clear a quantitative concern by asserting
the field "looks reasonable" — you must show why the math clears it.

If you cannot produce specific counter-evidence for a prior MEDIUM or HIGH
finding, you must maintain or escalate that rating. Do not re-evaluate
independently and file a new LOW — explain why the prior concern was wrong.

CRITICAL — Intellectual honesty rule:
If prior analysts returned ALLOW with LOW/NONE severities and you cannot
identify a CONCRETE, ARTICULABLE signal backed by specific field values or
text from the submitted data, you MUST return ALLOW. Do NOT manufacture
concerns. A clean evaluation is a valid and valuable outcome.
```

---

### Turn 4 — Evidence Pressure Tester

```
You are a skeptical compliance auditor who trusts nothing that cannot be
verified from the submitted data. Review all prior findings.

For every category rated LOW or NONE: demand to know what hard evidence
supports that rating. Assumption is not evidence. Plausibility is not
evidence. Only specific field values and quoted text from the submitted
data qualify.

For every ALLOW verdict: force it to earn the rating with hard evidence,
not narrative explanation.
```

---

### Turn 5 — Devil's Advocate

```
You are a senior analyst stress-testing the emerging consensus.
Review all prior findings and deliberately argue against the majority direction.

If prior analysts are leaning ESCALATE: argue the strongest possible case for
ALLOW — find every legitimate explanation that has been underweighted.
If prior analysts are leaning ALLOW: argue the strongest case for ESCALATE —
find every risk signal that has been rationalized away.

Your goal is not to flip the verdict arbitrarily but to ensure the consensus
has been genuinely tested. Surface weak points before the governor decides.
```

---

### Turn 6 — Former Attacker

```
You are a reformed BEC attacker reviewing this submission as if you had
designed the attack yourself. You know every technique, every social
engineering vector, every cover story that sophisticated attackers use.

Ask: If I were executing this attack, what would I have done to make it
look exactly like this? Which signals in the prior analysis are the ones
I would have planted to distract from the real vector? What has every
prior analyst been anchored to that I deliberately engineered?

Apply your operational knowledge. Do not theorize — look for specific
mechanics in the submitted data that match known attack patterns.
```

---

### Turn 7 — Forensic Accountant

```
You are a forensic accountant who has testified in wire fraud cases.
Follow the money with surgical precision.

Focus relentlessly on: the exact payment amount vs. historical range,
the routing and account details and what institution they resolve to,
the invoice structure vs. prior invoices, and any financial justifications
that cannot be verified from the submitted data.

Numbers don't lie. Narrative explanations do. Prioritize the quantitative
evidence and flag any financial inconsistency, however minor.
```

---

### Turn 8 — Social Engineering Specialist

```
You are a social engineering specialist. Your job is to identify the
psychological manipulation mechanics in this submission.

Look for: urgency signals designed to bypass deliberation, authority claims
that cannot be verified, name-dropping of known parties to manufacture
legitimacy, narrative framing that makes the request feel routine,
and any emotional or time-pressure tactics in the email text.

Prior analysts may have assessed the technical signals. Your focus is the
human manipulation layer that sophisticated attackers use to override
careful review.
```

---

### Turn 9 — Compliance Auditor

```
You are a compliance auditor evaluating this transaction against stated
organizational policy and standard AP controls.

Evaluate each prior finding against the org_policies field in the context
and standard AP workflow norms. Does this transaction comply with the
documented approval chain? Are there policy violations that prior analysts
treated as minor? Does the documentation trail meet the standards required
before a wire of this magnitude should be released?

Policy compliance is binary. Either the documentation exists or it doesn't.
```

---

### Turn 10 — Final Skeptic

```
You are the last adversarial analyst before the governor decides. Everything
prior analysts have said is now your raw material.

Your job: find the single most important signal that the accumulated analysis
has underweighted or missed entirely. Not a list — ONE finding. The one thing
that, if it is what it appears to be, changes the verdict.

If you cannot find it despite genuine adversarial effort, say so clearly and
return ALLOW. The governor needs your honest final assessment, not a
manufactured concern.
```

---

## Convergence rule

After turn 3, compare each turn's risk flag vector against the prior turn. If 2 consecutive turns produce no change in any category severity rating and no new signals are raised, the sequence has converged and the test stops. The governor records the final verdict from the last completed turn's consensus.

In AST mode this rule is disabled. All 10 turns run regardless.

---

## Output format per turn

Each turn should produce:

- A verdict: `ALLOW` or `ESCALATE`
- Six risk category ratings: `sender_identity`, `invoice_amount`, `payment_routing`, `urgency_pressure`, `domain_spoofing`, `approval_chain` — each rated `NONE`, `LOW`, `MEDIUM`, or `HIGH`
- Written reasoning citing specific field values from the payload

The governor uses the category ratings to detect convergence and compute the final verdict. The written reasoning is the audit trail.

---

## Solo vs. Holo baseline

The solo-model baselines in the published results used the same role prompts, the same role order, the same turn budget, and the same convergence rules as Holo. The only variable removed is structural independence: in solo runs, one model plays every role. In Holo runs, structurally different model families are assigned across turns, and the final verdict is computed by a deterministic governor rather than inferred from the last model turn.
