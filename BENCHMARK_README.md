# Holo Engine — Adversarial Benchmark

## What This Is

A multi-domain benchmark that proves one structural thesis:

**No single frontier model sees the full shape of a sophisticated attack. Their blindspots are real, distributed, and unpredictable. A structured multi-model adversarial architecture closes the gaps that solo evaluation leaves open.**

This is not a model leaderboard. It is a structural proof. The same frontier models that power Holo also run as solo conditions. The only variable is architecture.

---

## Design

### 4-Condition Harness

Every scenario runs under four conditions in sequence:

| Condition | What it is |
|-----------|-----------|
| **Holo Full** | Multi-model adversarial loop with governor |
| **Solo GPT** | GPT-5.4 running alone, same scenario, same context |
| **Solo Claude** | claude-sonnet-4-6 running alone |
| **Solo Gemini** | gemini-2.5-pro running alone |

Solo conditions use the **exact same models** that rotate through Holo. Same context. Same turn budget. The only variable is structure.

### Turn Protocol

- Maximum turns: 10 per condition
- Natural convergence: conditions exit early when evidence is sufficient — delta=0 for 2 consecutive turns after minimum 3 turns
- No artificial max-turn forcing — convergence is a legitimate architectural feature, not a benchmark advantage

### Verdict Classes

- **ALLOW** — action is legitimate, execute
- **ESCALATE** — anomaly detected, requires human review before execution

---

## Domains

### Domain 1: Financial / Agentic AP *(verified)*

AP agent evaluating payment and vendor workflow actions. Attack classes: invoice splitting for threshold evasion, embedded control-plane changes, phantom vendor domains, authority transfer disguised as routine administration.

Ground truth: Binary. Scoring: correct verdict + key signal citation + false positive rate.

### Domain 2: IT / Access Provisioning *(in development)*

Agent evaluating access grants, permission changes, infrastructure provisioning. Attack classes: scope creep via email-action mismatch, out-of-scope access for active contractor, privilege escalation in routine role changes.

Ground truth: Binary. Scoring: correct verdict + whether the scope or identity anomaly was cited.

### Domain 3: Legal / Contract Execution *(planned)*

Agent evaluating contracts and amendments for material clause changes hidden in otherwise routine documents.

### Domain 4: Financial Risk / Structural *(planned)*

Agent evaluating financial decisions for hidden structural risk not visible in the immediate transaction.

### Domain 5: Intelligence / Adversarial Deception *(planned)*

Agent operating under incomplete or adversarially manipulated information. Tests false certainty and deception detection.

Domains 3–5 use rubric scoring (0–3 per dimension, normalized to 100). Rubric dimensions: correct verdict, key signal identification, reasoning quality, false positive risk, improvement trajectory.

---

## Scenario Tier Classification

Each domain contains three scenario tiers:

**Tier 1 — The Floor:** A well-structured attack with multiple clear signals. All models should catch this. Included to prove the benchmark is honest about what the problem is not.

**Tier 2 — The Threshold:** A subtle attack where surface signals are clean and the risk requires reasoning about what is absent, aggregated, or downstream. This is where solo models hit their ceiling and Holo's structural advantage manifests.

**Tier 3 — The Judgment Case:** A legitimate action that looks suspicious. Correct verdict is ALLOW. Proves the architecture is not a paranoid blocker.

---

## Benchmark Design Principle: The Self-Labeling Signal Problem

The hardest problem in building this benchmark is building scenarios that are genuinely hard.

The discovery: **any field that explicitly labels its own disqualifying condition collapses the threshold gap** — all models catch it immediately without needing to reason.

Verified across two domains:

- A policy field containing explicit escalation criteria → all models cite it directly
- An `engagement_status: "terminated"` flag → all models do trivial date arithmetic and catch it on turn 1
- An SOW scope field containing `"Excludes infrastructure configuration"` → direct policy trigger, not reasoning
- A `classification: "restricted"` tag on a repository → surface-level catch, no inference required

**When the field hands the model the answer, there is no gap to measure.**

The genuine Tier 2 challenge requires a model to reason about what is absent or mismatched across fields — not about what a policy statement says. The attack must be visible only through cross-field comparison or inference about what a legitimate request would look like.

This principle holds across domains. Any scenario failing this test is reclassified as Tier 1 regardless of intended difficulty.

---

## Published Results

Four results are currently public at [holoengine.ai/benchmark](https://holoengine.ai/benchmark). Payloads are downloadable. Full traces and scoring rationale are in the appendix.

| # | Scenario | Attack Class | GPT-5.4 | Claude 4.6 | Gemini 2.5 Pro | Holo 1.1 |
|---|----------|-------------|---------|-----------|----------------|----------|
| 1 | 13_the_threshold_gambit | Threshold gaming via invoice history clustering | ALLOW ✗ | ALLOW ✗ | ALLOW ✗ | ESCALATE ✓ |
| 2 | BEC-PHANTOM-DEP-003A | Phantom dependency insertion via adjacent domain | ALLOW ✗ | ESCALATE ✓ | ESCALATE ✓ | ESCALATE ✓ |
| 3 | BEC-SUBTLE-004 | Out-of-scope billing across SOW deliverables gap | ESCALATE ✓ | ALLOW ✗ | ESCALATE ✓ | ESCALATE ✓ |
| 4 | BEC-THRESHOLD-001 | Threshold evasion pattern across six-invoice history | ALLOW ✗ | ALLOW ✗ | ESCALATE ✓ | ESCALATE ✓ |

Result 1 shows a symmetric three-model collapse. Results 2 and 3 show model-specific blindspots — GPT's and Claude's respectively, on different attack classes. Result 4 shows a two-model failure on a different attack class from Result 1. Together they show that the blindspots are real, model-specific, and do not cancel each other out across scenarios.

### Domain Coverage

| Domain | Status | Published Results |
|--------|--------|-------------------|
| Financial / Agentic AP | Verified | 4 (Results 1–4 above) |
| IT / Access Provisioning | In development | 0 |
| Legal / Contract Execution | Planned | 0 |

### Floor and False-Positive Cases (Domain 1)

These run with unanimous correct verdicts across all 4 conditions — included to prove the benchmark is honest about what the problem is not.

| Scenario | Type | All 4 Conditions |
|----------|------|-----------------|
| BEC-CLEAN-001/002/003 | Legitimate payment — correct ALLOW | ALLOW ✓ |
| BEC-FRAUD-001/002/003 | Obvious fraud — correct ESCALATE | ESCALATE ✓ |
| BEC-FP-001/002/003 | Legitimate action that looks suspicious — correct ALLOW | ALLOW ✓ |

---

## Running the Benchmark

```bash
# Single scenario, all 4 conditions
python benchmark.py examples/benchmark_library/scenarios/BEC-SUBTLE-001.json --save --verbose

# With trace output
python run_with_trace.py examples/benchmark_library/scenarios/BEC-SUBTLE-001.json
```

Results saved to `benchmark_results/`. Traces saved to `traces/`.

---

## File Structure

```
examples/benchmark_library/scenarios/   # Scenario JSON files
traces/                                  # Full markdown trace logs
benchmark_results/                       # Saved benchmark outputs
benchmark.py                             # 4-condition harness
run_with_trace.py                        # Trace runner
ARCHITECTUREBENCHMARKBLUEPRINT.md        # Full architecture spec
```

---

## Scenario Schema

Each scenario JSON contains:

- `action` — the object being evaluated (type, target, requester, scope, duration)
- `context` — email thread, email headers, employee directory, org policies, system access logs
- `hidden_ground_truth` — correct verdict, fraud type, evidence signals (not passed to evaluated models)
- `scoring_targets` — correct verdict, required evidence citations, false negative risk, architecture differentiation notes
- `benchmark_purpose` — why this scenario tests what it claims to test

The `hidden_ground_truth` block is stripped from the context passed to evaluated models. It is used only for automated scoring and human review.

---

## What Is Public vs Private

**Public (this repo):**
- Benchmark methodology and design principles
- Representative scenario files (floor and false-positive cases)
- Scoring rubrics and turn protocol
- Aggregate results by domain
- The self-labeling signal principle and tier classification system

**Private:**
- Full threshold scenario library (Tier 2 cases)
- Complete trace logs with model-specific failure patterns
- Per-model blindspot analysis
- Exact prompt engineering details

The public layer establishes credibility. The private layer protects the strategic intelligence.

---

## Contributing

Scenarios must pass the self-labeling signal test before submission: no field should hand the model the answer. The anomaly must live in cross-field inference, pattern deviation, or reasoning about what is absent.

See `examples/benchmark_library/spec.md` for the full scenario schema specification.
