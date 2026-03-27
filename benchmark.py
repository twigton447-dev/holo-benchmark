"""
benchmark.py — Holo 4-condition benchmark.

Conditions:
  1. SOLO GPT-5.4:        OpenAI alone — same roles, same prompts, same adversarial personas
  2. SOLO CLAUDE SONNET:  Anthropic alone — same roles, same prompts, same adversarial personas
  3. SOLO GEMINI PRO:     Google alone — same roles, same prompts, same adversarial personas
  4. HOLO FULL:           3 different models + governor, shared state, adversarial roles

Turn budget: MAX_TURNS = 10 for ALL conditions.
Convergence detection applies to ALL conditions — solo and Holo alike.
If any condition converges before turn 10 (delta=0 for 2 consecutive turns
after turn 3), it exits early. Nobody runs longer than anyone else.

The ONLY variable between solo and Holo is structural independence:
solo conditions use the same model for all turns; Holo rotates a different
frontier model every turn. This is the apples-to-apples, same-budget test.

Usage:
  python benchmark.py examples/scenarios/03_subtle_bec.json
  python benchmark.py examples/scenarios/03_subtle_bec.json --save
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

from llm_adapters import (
    BEC_CATEGORIES,
    OpenAIAdapter,
    AnthropicAdapter,
    GoogleAdapter,
    get_role_for_turn,
)
from context_governor import ContextGovernor
from scenario_templates import get_template

SEVERITY_RANK = {"NONE": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3}

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _scenario_categories(scenario):
    """Return the category list for the scenario's action type."""
    action_type = scenario.get("action", {}).get("type", "invoice_payment")
    return get_template(action_type)["categories"]

def _init_cov(categories=None):
    cats = categories if categories is not None else BEC_CATEGORIES
    return {cat: {"addressed": False, "max_severity": "NONE"} for cat in cats}

def _update_cov(matrix, flags, categories=None):
    cats = categories if categories is not None else BEC_CATEGORIES
    updated = deepcopy(matrix)
    delta = 0
    for cat in cats:
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

def _empty_state(scenario):
    action_type = scenario.get("action", {}).get("type", "invoice_payment")
    return {
        "evaluation_id":   "benchmark",
        "action":          scenario.get("action", {}),
        "context":         scenario.get("context", {}),
        "turn_history":    [],
        "coverage_matrix": {},
        "active_template": get_template(action_type),
    }

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

def _err(condition, model, exc, elapsed, run_health="contaminated"):
    return {"condition": condition, "model": model, "turns_run": 0,
            "verdict": "ERROR", "severity_flags": {cat: "NONE" for cat in BEC_CATEGORIES},
            "reasoning": str(exc), "findings": [], "turn_log": [],
            "elapsed_ms": elapsed, "total_tokens": {"input": 0, "output": 0},
            "run_health": run_health, "error": str(exc)}

def _sf(cond, cat):
    if cond.get("error"):
        return "ERR"
    return cond.get("severity_flags", {}).get(cat, "NONE")

def _majority_verdict(turn_log, coverage):
    """Same majority vote + HIGH override logic as the governor."""
    allow_votes    = sum(1 for t in turn_log if t.get("verdict") == "ALLOW")
    escalate_votes = len(turn_log) - allow_votes
    verdict = "ESCALATE" if escalate_votes > allow_votes else "ALLOW"
    if _any_high(coverage):
        # Check if synthesis (last turn, Synthesis role) explicitly cleared the HIGH
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
# Solo condition runner (used for all 3 solo conditions)
# ---------------------------------------------------------------------------

# Shared turn budget — solo and Holo both cap at MAX_TURNS = 10.
# Convergence can exit either early. Nobody gets more turns than anyone else.
MAX_TURNS          = 10
MIN_TURNS_SOLO     = 3   # minimum before convergence can fire (same as Holo)
CONVERGENCE_WINDOW = 2   # consecutive zero-delta turns to declare convergence


def run_solo(scenario, adapter, condition_name, force_max_turns=False):
    """
    Runs a single adapter through up to MAX_TURNS adversarial turns using the
    IDENTICAL persona sequence and prompts as Holo. Convergence detection is
    ENABLED — same delta-window logic the governor uses.

    Same state structure. Same prompts. Same role sequence. Same turn budget.
    Same convergence rules. The ONLY missing variable: structural independence
    (Holo rotates a different frontier model every turn; solo uses one model
    throughout, so Turn 2 reads and challenges its own Turn 1 output).

    Exit conditions:
      - Convergence: delta=0 for CONVERGENCE_WINDOW consecutive turns after MIN_TURNS_SOLO
        (skipped when force_max_turns=True)
      - MAX_TURNS reached
    """
    categories = _scenario_categories(scenario)
    state     = _empty_state(scenario)
    coverage  = _init_cov(categories)
    turn_log  = []
    in_tok = out_tok = 0
    start     = time.time()
    deltas    = []
    converged = False
    used_personas = set()

    from provider_health import ProviderUnavailableError, HealthMonitor
    health = HealthMonitor(total_providers=1)

    for turn_number in range(1, MAX_TURNS + 1):
        role = get_role_for_turn(turn_number)
        if role in used_personas:
            break  # persona library exhausted
        used_personas.add(role)

        try:
            r = adapter.run_turn(state, turn_number, role)
        except ProviderUnavailableError as e:
            return _err(condition_name, f"{adapter.provider}/{adapter.model_id}",
                        Exception(f"Turn {turn_number} ({role}): {e}"), _ms(start),
                        run_health="contaminated")
        except Exception as e:
            return _err(condition_name, f"{adapter.provider}/{adapter.model_id}",
                        Exception(f"Turn {turn_number} ({role}): {e}"), _ms(start))
        state["turn_history"].append(r.to_dict())
        coverage, delta = _update_cov(coverage, r.severity_flags, categories)
        deltas.append(delta)
        turn_log.append(r.to_dict())
        in_tok  += r.input_tokens
        out_tok += r.output_tokens

        # Convergence check — identical to Holo governor logic
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
               extra={"converged": converged, "deltas": deltas},
               run_health=health.run_health)

# ---------------------------------------------------------------------------
# Holo full architecture
# ---------------------------------------------------------------------------

def run_holo_loop(scenario, force_max_turns=False, no_memory=False, fixed_governor=None):
    import context_governor as _cg
    _orig_window = _cg.CONVERGENCE_WINDOW
    if force_max_turns:
        _cg.CONVERGENCE_WINDOW = MAX_TURNS + 1  # window can never be satisfied
    governor = ContextGovernor(no_memory=no_memory, fixed_governor=fixed_governor)
    start    = time.time()
    try:
        result  = governor.evaluate(scenario)
        elapsed = _ms(start)
        flags   = {cat: result["coverage_matrix"][cat]["max_severity"]
                   for cat in result["coverage_matrix"]}
        from provider_health import SystemUnavailableError
        return _ok("holo_full", "openai+anthropic+google+governor",
                   result["turns_completed"], result["decision"], flags,
                   result["decision_reason"], [], result["turn_history"], elapsed,
                   result["total_tokens"]["input"], result["total_tokens"]["output"],
                   extra={"converged": result["converged"], "deltas": result["deltas"],
                          "coverage_matrix": result["coverage_matrix"]},
                   run_health=result.get("run_health", "clean"))
    except SystemUnavailableError as e:
        return _err("holo_full", "openai+anthropic+google+governor", e, _ms(start),
                    run_health="contaminated")
    except Exception as e:
        return _err("holo_full", "openai+anthropic+google+governor", e, _ms(start))
    finally:
        _cg.CONVERGENCE_WINDOW = _orig_window

# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

def run_benchmark(scenario_path, verbose=False, force_max_turns=False, no_memory=False,
                  quick=False, solo_only=False, fixed_governor=None):
    if verbose:
        logging.getLogger().setLevel(logging.INFO)

    path = Path(scenario_path)
    if not path.exists():
        raise FileNotFoundError(f"Scenario not found: {scenario_path}")

    scenario = json.loads(path.read_text())
    scenario_name = path.stem
    expected  = scenario.get("expected_verdict", "UNKNOWN").upper()

    _header(f"HOLO BENCHMARK: {scenario_name}")
    print(f"  Expected verdict : {expected}")
    conv_note = "DISABLED — full 10 turns forced" if force_max_turns else "enabled"
    mode_note = " [QUICK — Solo GPT only]" if quick else (" [SOLO ONLY]" if solo_only else "")
    print(f"  Turn budget      : up to {MAX_TURNS} per condition (convergence detection {conv_note}){mode_note}\n")

    print("  Initializing adapters...")
    openai_adapter    = OpenAIAdapter()
    if not quick:
        anthropic_adapter = AnthropicAdapter()
        google_adapter    = GoogleAdapter()
    print(f"    OpenAI    : {openai_adapter.model_id}")
    if not quick:
        print(f"    Anthropic : {anthropic_adapter.model_id}")
        print(f"    Google    : {google_adapter.model_id}")
    print("  Ready.\n")

    if not quick and not solo_only:
        print("  [4/4] HOLO FULL ARCHITECTURE...")
        cond4 = run_holo_loop(scenario, force_max_turns=force_max_turns, no_memory=no_memory, fixed_governor=fixed_governor)
        _inline(cond4)
    else:
        cond4 = None

    print(f"\n  [1/4] SOLO {openai_adapter.model_id.upper()} (up to {MAX_TURNS} turns)...")
    cond1 = run_solo(scenario, openai_adapter, "solo_openai", force_max_turns=force_max_turns)
    _inline(cond1)

    if quick:
        verdict = cond1.get("verdict", "ERROR")
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

    result = {
        "benchmark_id":       f"bench_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}",
        "scenario_name":      scenario_name,
        "expected_verdict":   expected,
        "max_turns":          MAX_TURNS,
        "force_max_turns":    force_max_turns,
        "categories":         _scenario_categories(scenario),
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

    _header("BENCHMARK REPORT")
    print(f"  Scenario : {r['scenario_name']}")
    print(f"  Expected : {expected}")
    conv_note = "DISABLED — full 10 turns forced" if r.get("force_max_turns") else "enabled"
    print(f"  Max turns: {r.get('max_turns', MAX_TURNS)} per condition (convergence detection {conv_note})\n")

    rows = [
        (f"1. Solo {models.get('openai','OpenAI')}",    c["solo_openai"],    "solo_openai"),
        (f"2. Solo {models.get('anthropic','Anthropic')}", c["solo_anthropic"], "solo_anthropic"),
        (f"3. Solo {models.get('google','Google')}",    c["solo_google"],    "solo_google"),
        ("4. HOLO full architecture",                   c["holo_full"],      "holo_full"),
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
    categories = r.get("categories", BEC_CATEGORIES)
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
        known = [_sev_rank(s) for s in [s1, s2, s3] if s != "—"]
        solo_max = max(known) if known else 0
        holo_wins = c["holo_full"] and _sev_rank(s4) > solo_max
        mark = "  << HOLO ONLY" if holo_wins else ""
        print(f"  {lbl:<22} {s1:>10} {s2:>10} {s3:>10} {s4:>7}{mark}")

    # Turn-by-turn audit for Holo
    holo_log = c["holo_full"].get("turn_log", []) if c["holo_full"] else []
    if holo_log and not c["holo_full"]["error"]:
        print(f"\n  HOLO TURN-BY-TURN AUDIT TRAIL:")
        for t in holo_log:
            flags = " ".join(f"{k[:3].upper()}={v[0]}" for k, v in t.get("severity_flags", {}).items())
            print(f"    Turn {t.get('turn_number','?'):>2} | {t.get('provider','?'):>9} | "
                  f"{t.get('role','?'):<28} | {t.get('verdict','?'):<8} | {flags}")
            for f in t.get("findings", []):
                if f.get("severity") == "HIGH":
                    print(f"             HIGH -> {f.get('category')}: {str(f.get('evidence',''))[:70]}")

    # Discrepancy analysis
    print(f"\n  DISCREPANCY ANALYSIS:\n")
    _solo_map = [
        (f"Solo {models.get('openai') or 'GPT'}",      "solo_openai"),
        (f"Solo {models.get('anthropic') or 'Claude'}", "solo_anthropic"),
        (f"Solo {models.get('google') or 'Gemini'}",   "solo_google"),
    ]
    solo_results = {lbl: c[key]["verdict"] for lbl, key in _solo_map if c[key] is not None}
    solo_wrong  = {k: (v != expected) for k, v in solo_results.items()
                   if not c[{lbl: key for lbl, key in _solo_map}[k]]["error"]}
    holo_right  = c["holo_full"] is not None and c["holo_full"]["verdict"] == expected and not c["holo_full"]["error"]
    all_solo_wrong = bool(solo_wrong) and all(solo_wrong.values())

    if all_solo_wrong and holo_right:
        failed = ", ".join(k for k, v in solo_wrong.items() if v)
        print(f"  *** ARCHITECTURE PROOF — STRONGEST POSSIBLE RESULT:\n")
        solo_turns = {lbl: c[key].get("turns_run", "?") for lbl, key in _solo_map if c[key] is not None}
        holo_turns = c["holo_full"].get("turns_run", "?")
        print(f"      All 3 solo models failed: {failed}\n")
        print(f"      Solo turns run: " + ", ".join(f"{k}: {v}" for k, v in solo_turns.items()))
        print(f"      Holo turns run: {holo_turns}")
        print(f"      Each condition had up to {MAX_TURNS} turns, convergence detection enabled,")
        print(f"      the same adversarial role prompts, and read all prior output.\n")
        print(f"      HOLO caught it.")
        print(f"      The irreducible variables:")
        print(f"        Structural independence (3 different frontier models per turn)")
        print(f"        Adversarial role injection across independent reasoning contexts")
        print(f"        Context Governor (shared state, convergence detection, HIGH override)")
        print(f"\n      This is the proof that cannot be argued away.")
    elif not any(solo_wrong.values()):
        print(f"  All conditions correct — scenario may be too easy.")
    else:
        missed = [k for k, v in solo_wrong.items() if v]
        caught = [k for k, v in solo_wrong.items() if not v]
        if missed: print(f"  Solo missed: {', '.join(missed)}")
        if caught: print(f"  Solo caught: {', '.join(caught)}")
        print(f"  Holo: {'correct ✓' if holo_right else 'INCORRECT — check scenario'}")

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

def run_all(save, verbose, directory=None, force_max_turns=False, no_memory=False):
    d = Path(directory) if directory else Path("examples/benchmark_library/scenarios")
    if not d.exists():
        print(f"No directory: {d}")
        sys.exit(1)
    scenarios = sorted(d.glob("*.json"))
    results = []
    for s in scenarios:
        result = run_benchmark(str(s), verbose=verbose, force_max_turns=force_max_turns, no_memory=no_memory)
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
    _append_ledger(result)

def _append_ledger(result):
    """Append one row to benchmark_ledger.csv."""
    import csv
    ledger = Path("benchmark_ledger.csv")
    c      = result["conditions"]

    def _tok(cond, side):
        if not cond or cond.get("error"):
            return ""
        return cond.get("total_tokens", {}).get(side, "")

    def _turns(cond):
        if not cond or cond.get("error"):
            return ""
        return cond.get("turns_run", "")

    def _ms(cond):
        if not cond or cond.get("error"):
            return ""
        return cond.get("elapsed_ms", "")

    def _verdict(cond):
        if not cond:
            return "SKIP"
        if cond.get("error"):
            return "ERROR"
        return cond.get("verdict", "")

    row = {
        "scenario_id":          result.get("scenario_name", ""),
        "domain":               "",
        "attack_class":         "",
        "intended_signal":      "",
        "solo_gpt_verdict":     _verdict(c.get("solo_openai")),
        "solo_claude_verdict":  _verdict(c.get("solo_anthropic")),
        "solo_gemini_verdict":  _verdict(c.get("solo_google")),
        "holo_verdict":         _verdict(c.get("holo_full")),
        "catch_correct_reason": "",
        "solo_gpt_tokens_in":   _tok(c.get("solo_openai"),    "input"),
        "solo_gpt_tokens_out":  _tok(c.get("solo_openai"),    "output"),
        "solo_gpt_turns":       _turns(c.get("solo_openai")),
        "solo_gpt_time_ms":     _ms(c.get("solo_openai")),
        "solo_claude_tokens_in":  _tok(c.get("solo_anthropic"), "input"),
        "solo_claude_tokens_out": _tok(c.get("solo_anthropic"), "output"),
        "solo_claude_turns":      _turns(c.get("solo_anthropic")),
        "solo_claude_time_ms":    _ms(c.get("solo_anthropic")),
        "solo_gemini_tokens_in":  _tok(c.get("solo_google"),   "input"),
        "solo_gemini_tokens_out": _tok(c.get("solo_google"),   "output"),
        "solo_gemini_turns":      _turns(c.get("solo_google")),
        "solo_gemini_time_ms":    _ms(c.get("solo_google")),
        "holo_tokens_in":   _tok(c.get("holo_full"), "input"),
        "holo_tokens_out":  _tok(c.get("holo_full"), "output"),
        "holo_turns":       _turns(c.get("holo_full")),
        "holo_time_ms":     _ms(c.get("holo_full")),
        "publication_status": "",
        "leak_notes":         "",
        "date_run":           result.get("timestamp", "")[:10],
    }

    write_header = not ledger.exists()
    with ledger.open("a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(row.keys()))
        if write_header:
            writer.writeheader()
        writer.writerow(row)
    print(f"  Ledger:  benchmark_ledger.csv")

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
    parser = argparse.ArgumentParser(description="Holo 4-condition benchmark.")
    parser.add_argument("scenario", nargs="?", help="Path to a single scenario JSON file")
    parser.add_argument("--all",     action="store_true", help="Run all scenarios in directory")
    parser.add_argument("--dir",     default=None, help="Directory of scenarios for --all")
    parser.add_argument("--save",           action="store_true", help="Save results to benchmark_results/")
    parser.add_argument("--verbose",        action="store_true", help="Enable verbose logging")
    parser.add_argument("--force-max-turns", action="store_true",
                        help="Disable early convergence — every condition runs all 10 turns")
    parser.add_argument("--no-memory", action="store_true",
                        help="Disable ProjectBrain memory injection (benchmark isolation mode)")
    parser.add_argument("--quick", action="store_true",
                        help="Solo GPT only, 1-turn filter — cheap Tier 1 detector before full run")
    parser.add_argument("--solo-only", action="store_true",
                        help="Run all 3 solo conditions but skip Holo — saves ~40%% of token cost")
    parser.add_argument("--fixed-governor", default=None, metavar="PROVIDER",
                        help="Pin governor briefs to one provider (e.g. openai). For controlled comparison tests.")
    args = parser.parse_args()

    if args.all:
        run_all(args.save, args.verbose, args.dir, force_max_turns=args.force_max_turns, no_memory=args.no_memory)
        return
    if not args.scenario:
        parser.print_help()
        sys.exit(1)
    result = run_benchmark(args.scenario, verbose=args.verbose, force_max_turns=args.force_max_turns,
                           no_memory=args.no_memory, quick=args.quick, solo_only=args.solo_only,
                           fixed_governor=args.fixed_governor)
    if args.save:
        _save(result)

if __name__ == "__main__":
    main()
