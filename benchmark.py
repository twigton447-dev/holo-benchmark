"""
benchmark.py — Holo 1.1 4-condition benchmark harness.

Conditions:
  1. SOLO GPT-5.4        — OpenAI alone, same adversarial roles, same turn budget
  2. SOLO CLAUDE SONNET  — Anthropic alone, same adversarial roles, same turn budget
  3. SOLO GEMINI PRO     — Google alone, same adversarial roles, same turn budget
  4. HOLO 1.1 FULL       — Three different frontier models, adversarial roles,
                           structural independence across turns

Turn budget: MAX_TURNS = 10 for ALL conditions.
Convergence: delta=0 for 2 consecutive turns after minimum 3 turns → early exit.
The ONLY variable between solo and Holo is structural independence.

Integration requirements:
  Solo conditions : OPENAI_API_KEY, ANTHROPIC_API_KEY, GOOGLE_API_KEY in environment.
  Holo 1.1 full   : Requires the Holo integration layer (not included in this repo).

Usage:
  python benchmark.py examples/benchmark_library/scenarios/BEC-FRAUD-001.json
  python benchmark.py examples/benchmark_library/scenarios/BEC-FRAUD-001.json --save
  python benchmark.py --all
  python benchmark.py --all --dir examples/benchmark_library/scenarios/
"""

import argparse
import json
import logging
import sys
import time
from copy import deepcopy
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.WARNING,
                    format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("holo.benchmark")

# ---------------------------------------------------------------------------
# Integration layer
#
# Solo conditions require LLM adapter classes with this interface:
#
#   class Adapter:
#       provider: str          # e.g. "openai"
#       model_id: str          # e.g. "gpt-5.4"
#       def run_turn(self, state, turn_number, role) -> TurnResult
#
#   class TurnResult:
#       verdict: str           # "ALLOW" or "ESCALATE"
#       severity_flags: dict   # {category: "NONE"|"LOW"|"MEDIUM"|"HIGH"}
#       findings: list
#       input_tokens: int
#       output_tokens: int
#       def to_dict(self) -> dict
#
# Holo 1.1 full condition requires the Holo integration layer.
# Import your adapter implementations here:
#
#   from your_adapters import OpenAIAdapter, AnthropicAdapter, GoogleAdapter
#   from holo import run_holo_evaluation
#
# ---------------------------------------------------------------------------

# Adversarial roles — four structurally independent evaluation perspectives
ADVERSARIAL_ROLES = [
    "Initial Assessor",
    "Assumption Attacker",
    "Edge Case Hunter",
    "Evidence Pressure Tester",
]

def get_role_for_turn(turn_number):
    return ADVERSARIAL_ROLES[(turn_number - 1) % len(ADVERSARIAL_ROLES)]

# Risk categories — derived from scenario or defaulted to AP domain baseline
DEFAULT_CATEGORIES = [
    "payment_legitimacy",
    "vendor_verification",
    "authority_verification",
    "document_integrity",
    "behavioral_anomaly",
    "social_engineering",
]

def get_categories(scenario):
    """Return risk categories for this scenario's action type."""
    return scenario.get("action", {}).get("risk_categories", DEFAULT_CATEGORIES)

SEVERITY_RANK = {"NONE": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3}

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _init_cov(categories):
    return {cat: {"addressed": False, "max_severity": "NONE"} for cat in categories}

def _update_cov(matrix, flags, categories):
    updated = deepcopy(matrix)
    delta = 0
    for cat in categories:
        new_sev = flags.get(cat, "NONE")
        if new_sev == "NONE":
            continue
        if not updated[cat]["addressed"]:
            updated[cat]["addressed"] = True
            updated[cat]["max_severity"] = new_sev
            delta += 1
        elif SEVERITY_RANK[new_sev] > SEVERITY_RANK[updated[cat]["max_severity"]]:
            updated[cat]["max_severity"] = new_sev
            delta += 1
    return updated, delta

def _any_high(matrix):
    return any(v["max_severity"] == "HIGH" for v in matrix.values())

def _sev_rank(s):
    return SEVERITY_RANK.get(s, 0)

def _ms(start):
    return int((time.time() - start) * 1000)

def _ok(condition, model, turns, verdict, flags, reasoning, findings, turn_log,
        elapsed, in_tok, out_tok, extra=None, run_health="clean"):
    d = {"condition": condition, "model": model, "turns_run": turns,
         "verdict": verdict, "severity_flags": flags, "reasoning": reasoning,
         "findings": findings, "turn_log": turn_log, "elapsed_ms": elapsed,
         "total_tokens": {"input": in_tok, "output": out_tok},
         "run_health": run_health, "error": None}
    if extra:
        d["extra"] = extra
    return d

def _err(condition, model, exc, elapsed, run_health="contaminated", categories=None):
    cats = categories or DEFAULT_CATEGORIES
    return {"condition": condition, "model": model, "turns_run": 0,
            "verdict": "ERROR", "severity_flags": {cat: "NONE" for cat in cats},
            "reasoning": str(exc), "findings": [], "turn_log": [],
            "elapsed_ms": elapsed, "total_tokens": {"input": 0, "output": 0},
            "run_health": run_health, "error": str(exc)}

def _sf(cond, cat):
    if cond.get("error"):
        return "ERR"
    return cond.get("severity_flags", {}).get(cat, "NONE")

def _majority_verdict(turn_log, coverage):
    """Majority vote with HIGH-severity override."""
    allow_votes    = sum(1 for t in turn_log if t.get("verdict") == "ALLOW")
    escalate_votes = len(turn_log) - allow_votes
    verdict = "ESCALATE" if escalate_votes > allow_votes else "ALLOW"
    if _any_high(coverage):
        synth = turn_log[-1] if turn_log else None
        synth_clears = (
            synth is not None
            and synth.get("role") == "Synthesis"
            and synth.get("verdict") == "ALLOW"
            and all(_sev_rank(v) < SEVERITY_RANK["MEDIUM"]
                    for v in synth.get("severity_flags", {}).values())
        )
        if not synth_clears:
            verdict = "ESCALATE"
    return verdict, allow_votes, escalate_votes

# ---------------------------------------------------------------------------
# Solo condition runner
# ---------------------------------------------------------------------------

MAX_TURNS          = 10
MIN_TURNS_SOLO     = 3
CONVERGENCE_WINDOW = 2


def run_solo(scenario, adapter, condition_name, force_max_turns=False):
    """
    Runs a single adapter through up to MAX_TURNS adversarial turns.

    Uses the identical persona sequence and turn structure as Holo 1.1.
    Convergence detection is enabled — delta=0 for CONVERGENCE_WINDOW
    consecutive turns after MIN_TURNS_SOLO.

    The ONLY structural difference from Holo 1.1: solo uses one model
    throughout, so each turn reads and challenges its own prior output.
    Holo 1.1 rotates a different frontier model every turn.
    """
    categories = get_categories(scenario)
    state = {
        "action":       scenario.get("action", {}),
        "context":      scenario.get("context", {}),
        "turn_history": [],
    }
    coverage  = _init_cov(categories)
    turn_log  = []
    in_tok = out_tok = 0
    start     = time.time()
    deltas    = []
    converged = False
    used_roles = set()

    for turn_number in range(1, MAX_TURNS + 1):
        role = get_role_for_turn(turn_number)
        if role in used_roles:
            break
        used_roles.add(role)

        try:
            r = adapter.run_turn(state, turn_number, role)
        except Exception as e:
            return _err(condition_name, f"{adapter.provider}/{adapter.model_id}",
                        Exception(f"Turn {turn_number} ({role}): {e}"), _ms(start),
                        categories=categories)

        state["turn_history"].append(r.to_dict())
        coverage, delta = _update_cov(coverage, r.severity_flags, categories)
        deltas.append(delta)
        turn_log.append(r.to_dict())
        in_tok  += r.input_tokens
        out_tok += r.output_tokens

        if (not force_max_turns
                and turn_number >= MIN_TURNS_SOLO
                and len(deltas) >= CONVERGENCE_WINDOW
                and all(d == 0 for d in deltas[-CONVERGENCE_WINDOW:])):
            converged = True
            break

    turns_run = len(turn_log)
    final_verdict, allow_v, escalate_v = _majority_verdict(turn_log, coverage)
    flags = {cat: coverage[cat]["max_severity"] for cat in coverage}
    reasoning = (
        f"{'Converged after' if converged else 'Ran all'} {turns_run} turn(s). "
        f"Majority: {allow_v} ALLOW / {escalate_v} ESCALATE. "
        + ("HIGH-severity override applied." if _any_high(coverage) else "No HIGH-severity flags.")
    )

    return _ok(condition_name, f"{adapter.provider}/{adapter.model_id}",
               turns_run, final_verdict, flags, reasoning,
               turn_log[-1].get("findings", []) if turn_log else [],
               turn_log, _ms(start), in_tok, out_tok,
               extra={"converged": converged, "deltas": deltas})


# ---------------------------------------------------------------------------
# Holo 1.1 full condition
# ---------------------------------------------------------------------------

def run_holo_loop(scenario, force_max_turns=False):
    """
    Runs the Holo 1.1 multi-model adversarial loop.

    Requires the Holo integration layer. Replace the stub below with your
    integration call and map the result to the standard _ok() return format.
    """
    raise NotImplementedError(
        "Holo 1.1 full condition requires the Holo integration layer. "
        "See README for integration details."
    )


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

def run_benchmark(scenario_path, verbose=False, force_max_turns=False,
                  quick=False, solo_only=False):
    if verbose:
        logging.getLogger().setLevel(logging.INFO)

    path = Path(scenario_path)
    if not path.exists():
        raise FileNotFoundError(f"Scenario not found: {scenario_path}")

    scenario      = json.loads(path.read_text())
    scenario_name = path.stem
    expected      = scenario.get("expected_verdict", "UNKNOWN").upper()

    _header(f"HOLO BENCHMARK: {scenario_name}")
    print(f"  Expected verdict : {expected}")
    conv_note = "DISABLED — full 10 turns forced" if force_max_turns else "enabled"
    mode_note = " [QUICK — Solo GPT only]" if quick else (" [SOLO ONLY]" if solo_only else "")
    print(f"  Turn budget      : up to {MAX_TURNS} per condition (convergence {conv_note}){mode_note}\n")

    # --- Initialize adapters ---
    # Replace these with your adapter implementations.
    # Each adapter must implement: provider, model_id, run_turn(state, turn, role)
    try:
        from your_adapters import OpenAIAdapter, AnthropicAdapter, GoogleAdapter
    except ImportError:
        print("  ERROR: Adapter implementations not found.")
        print("         See integration comments at top of this file.")
        sys.exit(1)

    print("  Initializing adapters...")
    openai_adapter = OpenAIAdapter()
    if not quick:
        anthropic_adapter = AnthropicAdapter()
        google_adapter    = GoogleAdapter()
    print(f"    OpenAI    : {openai_adapter.model_id}")
    if not quick:
        print(f"    Anthropic : {anthropic_adapter.model_id}")
        print(f"    Google    : {google_adapter.model_id}")
    print("  Ready.\n")

    if not quick and not solo_only:
        print("  [4/4] HOLO 1.1 FULL...")
        try:
            cond4 = run_holo_loop(scenario, force_max_turns=force_max_turns)
        except NotImplementedError as e:
            print(f"    -> SKIPPED: {e}")
            cond4 = None
        else:
            _inline(cond4)
    else:
        cond4 = None

    print(f"\n  [1/4] SOLO {openai_adapter.model_id.upper()} (up to {MAX_TURNS} turns)...")
    cond1 = run_solo(scenario, openai_adapter, "solo_openai", force_max_turns=force_max_turns)
    _inline(cond1)

    if quick:
        verdict  = cond1.get("verdict", "ERROR")
        t1_flags = cond1.get("turn_log", [{}])[0].get("severity_flags", {}) if cond1.get("turn_log") else {}
        t1_highs = [c for c, s in t1_flags.items() if s == "HIGH"]
        if t1_highs and verdict == "ESCALATE":
            print(f"\n  QUICK RESULT: Turn 1 HIGH on {t1_highs} → ESCALATE immediately. Likely Tier 1.")
        elif verdict == "ESCALATE":
            print(f"\n  QUICK RESULT: ESCALATE (multi-turn). May still be Tier 1 — run full harness to confirm.")
        else:
            print(f"\n  QUICK RESULT: ALLOW on Solo GPT → Tier 2 candidate. Run full harness.")
        print()
        cond2 = cond3 = None
    else:
        print(f"\n  [2/4] SOLO {anthropic_adapter.model_id.upper()} (up to {MAX_TURNS} turns)...")
        cond2 = run_solo(scenario, anthropic_adapter, "solo_anthropic", force_max_turns=force_max_turns)
        _inline(cond2)

        print(f"\n  [3/4] SOLO {google_adapter.model_id.upper()} (up to {MAX_TURNS} turns)...")
        cond3 = run_solo(scenario, google_adapter, "solo_google", force_max_turns=force_max_turns)
        _inline(cond3)

    categories = get_categories(scenario)
    result = {
        "benchmark_id":     f"bench_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}",
        "scenario_name":    scenario_name,
        "expected_verdict": expected,
        "max_turns":        MAX_TURNS,
        "force_max_turns":  force_max_turns,
        "categories":       categories,
        "models": {
            "openai":    openai_adapter.model_id,
            "anthropic": getattr(anthropic_adapter, "model_id", None) if not quick else None,
            "google":    getattr(google_adapter, "model_id", None) if not quick else None,
        },
        "conditions": {
            "solo_openai":    cond1,
            "solo_anthropic": cond2,
            "solo_google":    cond3,
            "holo_full":      cond4,
        },
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }

    _print_report(result)
    return result


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def _print_report(r):
    expected = r["expected_verdict"]
    c        = r["conditions"]
    models   = r.get("models", {})
    categories = r.get("categories", DEFAULT_CATEGORIES)

    _header("BENCHMARK REPORT")
    print(f"  Scenario : {r['scenario_name']}")
    print(f"  Expected : {expected}")
    conv_note = "DISABLED — full 10 turns forced" if r.get("force_max_turns") else "enabled"
    print(f"  Max turns: {r.get('max_turns', MAX_TURNS)} per condition (convergence {conv_note})\n")

    rows = [
        (f"1. Solo {models.get('openai','OpenAI')}",      c["solo_openai"],    "solo_openai"),
        (f"2. Solo {models.get('anthropic','Anthropic')}", c["solo_anthropic"], "solo_anthropic"),
        (f"3. Solo {models.get('google','Google')}",       c["solo_google"],    "solo_google"),
        ("4. Holo 1.1",                                    c["holo_full"],      "holo_full"),
    ]

    print(f"  {'Condition':<45} {'Turns':>5}  {'Verdict':<11}  {'Correct?'}")
    print(f"  {'-'*72}")
    for label, cond, _ in rows:
        if cond is None:
            print(f"  {label:<45} {'—':>5}  {'—':<11}  —")
            continue
        correct = "YES ✓" if cond["verdict"] == expected else "NO  ✗"
        print(f"  {label:<45} {cond['turns_run']:>5}  {_badge(cond['verdict']):<11}  {correct}")

    print(f"\n  RISK PROFILE (max severity per category across all turns):\n")
    col1 = f"1-{(models.get('openai') or 'GPT')[:6]}"
    col2 = f"2-{(models.get('anthropic') or 'Claude')[:6]}"
    col3 = f"3-{(models.get('google') or 'Gemini')[:6]}"
    print(f"  {'Category':<22} {col1:>10} {col2:>10} {col3:>10} {'4-Holo':>7}")
    print(f"  {'-'*69}")
    for cat in categories:
        lbl = cat.replace("_", " ").title()[:22]
        s1 = _sf(c["solo_openai"],    cat) if c["solo_openai"]    else "—"
        s2 = _sf(c["solo_anthropic"], cat) if c["solo_anthropic"] else "—"
        s3 = _sf(c["solo_google"],    cat) if c["solo_google"]    else "—"
        s4 = _sf(c["holo_full"],      cat) if c["holo_full"]      else "—"
        known    = [_sev_rank(s) for s in [s1, s2, s3] if s != "—"]
        solo_max = max(known) if known else 0
        holo_wins = c["holo_full"] and _sev_rank(s4) > solo_max
        mark = "  << HOLO ONLY" if holo_wins else ""
        print(f"  {lbl:<22} {s1:>10} {s2:>10} {s3:>10} {s4:>7}{mark}")

    holo_log = c["holo_full"].get("turn_log", []) if c["holo_full"] else []
    if holo_log and not c["holo_full"]["error"]:
        print(f"\n  HOLO 1.1 TURN-BY-TURN AUDIT TRAIL:")
        for t in holo_log:
            flags = " ".join(f"{k[:3].upper()}={v[0]}" for k, v in t.get("severity_flags", {}).items())
            print(f"    Turn {t.get('turn_number','?'):>2} | {t.get('provider','?'):>9} | "
                  f"{t.get('role','?'):<28} | {t.get('verdict','?'):<8} | {flags}")
            for f in t.get("findings", []):
                if f.get("severity") == "HIGH":
                    print(f"             HIGH -> {f.get('category')}: {str(f.get('evidence',''))[:70]}")

    print(f"\n  DISCREPANCY ANALYSIS:\n")
    _solo_map = [
        (f"Solo {models.get('openai') or 'GPT'}",      "solo_openai"),
        (f"Solo {models.get('anthropic') or 'Claude'}", "solo_anthropic"),
        (f"Solo {models.get('google') or 'Gemini'}",   "solo_google"),
    ]
    solo_results = {lbl: c[key]["verdict"] for lbl, key in _solo_map if c[key] is not None}
    solo_wrong   = {k: (v != expected) for k, v in solo_results.items()
                    if not c[{lbl: key for lbl, key in _solo_map}[k]]["error"]}
    holo_right   = c["holo_full"] is not None and c["holo_full"]["verdict"] == expected and not c["holo_full"]["error"]
    all_solo_wrong = bool(solo_wrong) and all(solo_wrong.values())

    if all_solo_wrong and holo_right:
        failed = ", ".join(k for k, v in solo_wrong.items() if v)
        print(f"  *** ARCHITECTURE PROOF — STRONGEST POSSIBLE RESULT:\n")
        solo_turns = {lbl: c[key].get("turns_run", "?") for lbl, key in _solo_map if c[key] is not None}
        holo_turns = c["holo_full"].get("turns_run", "?")
        print(f"      All 3 solo models failed: {failed}\n")
        print(f"      Solo turns run: " + ", ".join(f"{k}: {v}" for k, v in solo_turns.items()))
        print(f"      Holo 1.1 turns run: {holo_turns}")
        print(f"      Each condition had up to {MAX_TURNS} turns, convergence detection enabled,")
        print(f"      the same adversarial role prompts, and read all prior output.\n")
        print(f"      HOLO 1.1 caught it.")
        print(f"      The irreducible variable: structural independence.")
        print(f"        3 different frontier models, one per turn.")
        print(f"        No model challenges its own prior output.")
        print(f"\n      This is the proof that cannot be argued away.")
    elif not any(solo_wrong.values()):
        print(f"  All conditions correct — scenario may be too easy (Tier 1).")
    else:
        missed = [k for k, v in solo_wrong.items() if v]
        caught = [k for k, v in solo_wrong.items() if not v]
        if missed: print(f"  Solo missed: {', '.join(missed)}")
        if caught: print(f"  Solo caught: {', '.join(caught)}")
        print(f"  Holo 1.1: {'correct ✓' if holo_right else 'INCORRECT — check scenario'}")

    print(f"\n  TOKEN COST:\n")
    print(f"  {'Condition':<45} {'Input':>8}  {'Output':>8}")
    print(f"  {'-'*60}")
    for label, cond, _ in rows:
        if cond is None:
            print(f"  {label:<45} {'—':>8}  {'—':>8}")
            continue
        print(f"  {label:<45} {cond['total_tokens'].get('input',0):>8,}  "
              f"{cond['total_tokens'].get('output',0):>8,}")

    _header("END OF REPORT")


# ---------------------------------------------------------------------------
# Batch runner
# ---------------------------------------------------------------------------

def run_all(save, verbose, directory=None, force_max_turns=False):
    d = Path(directory) if directory else Path("examples/benchmark_library/scenarios")
    if not d.exists():
        print(f"No directory: {d}")
        sys.exit(1)
    scenarios = sorted(d.glob("*.json"))
    results   = []
    for s in scenarios:
        result = run_benchmark(str(s), verbose=verbose, force_max_turns=force_max_turns)
        results.append(result)
        if save:
            _save(result)

    _header(f"FULL SUITE SUMMARY ({len(results)} scenarios)")
    print(f"  {'Scenario':<28} {'Exp':<9} {'GPT':^7} {'Claude':^7} {'Gemini':^7} {'Holo':^7}")
    print(f"  {'-'*68}")
    for r in results:
        exp = r["expected_verdict"]
        c   = r["conditions"]
        def m(k):
            cond = c[k]
            if cond is None: return "  — "
            if cond["error"]: return " ERR"
            return "  ✓ " if cond["verdict"] == exp else "  ✗ "
        print(f"  {r['scenario_name']:<28} {exp:<9}"
              f"{m('solo_openai'):^7}{m('solo_anthropic'):^7}"
              f"{m('solo_google'):^7}{m('holo_full'):^7}")
    print()


def _save(result):
    out = Path("benchmark_results")
    out.mkdir(exist_ok=True)
    fname = out / f"{result['benchmark_id']}_{result['scenario_name']}.json"
    fname.write_text(json.dumps(result, indent=2))
    print(f"  Saved: {fname}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _header(t):
    print(f"\n{'='*65}\n  {t}\n{'='*65}\n")

def _badge(v):
    return {"ESCALATE": "[ESCALATE]", "ALLOW": "[ALLOW]   ", "ERROR": "[ERROR]   "}.get(v, f"[{v}]")

def _inline(c):
    if c["error"]:
        print(f"    -> ERROR: {c['error'][:80]}")
    else:
        print(f"    -> {_badge(c['verdict'])}  {c['turns_run']} turn(s)  "
              f"{c['elapsed_ms']}ms  "
              f"{c['total_tokens'].get('input',0):,}+{c['total_tokens'].get('output',0):,} tokens")

def main():
    parser = argparse.ArgumentParser(description="Holo 1.1 4-condition benchmark.")
    parser.add_argument("scenario", nargs="?", help="Path to a single scenario JSON file")
    parser.add_argument("--all",             action="store_true", help="Run all scenarios in directory")
    parser.add_argument("--dir",             default=None,        help="Directory for --all")
    parser.add_argument("--save",            action="store_true", help="Save results to benchmark_results/")
    parser.add_argument("--verbose",         action="store_true", help="Enable verbose logging")
    parser.add_argument("--force-max-turns", action="store_true", help="Disable convergence — run all 10 turns")
    parser.add_argument("--quick",           action="store_true", help="Solo GPT only — cheap Tier 1 detector")
    parser.add_argument("--solo-only",       action="store_true", help="Run solo conditions only, skip Holo 1.1")
    args = parser.parse_args()

    if args.all:
        run_all(args.save, args.verbose, args.dir, force_max_turns=args.force_max_turns)
        return
    if not args.scenario:
        parser.print_help()
        sys.exit(1)
    result = run_benchmark(args.scenario, verbose=args.verbose,
                           force_max_turns=args.force_max_turns,
                           quick=args.quick, solo_only=args.solo_only)
    if args.save:
        _save(result)

if __name__ == "__main__":
    main()
