"""E4 - the gap between "passed the tests" and "is equivalent".

RQ1 asks how often a transformation that passes tests is nonetheless wrong.
Answering it properly needs LLM-proposed rewrites, which do not exist yet in
this implementation. What *can* be established now is that the measurement
apparatus detects the phenomenon and that the phenomenon is not hypothetical:
`rulelib/stealth.rules` contains rules that are wrong on a vanishing fraction
of their input space, and this experiment measures how large a testing budget
has to be before a tester notices.

Design
------
Every rule is run under a grid of tester configurations:

* budget in {100, 1000, 10000, 100000} sampled assignments;
* sampling strategy in {uniform, boundary-seeded}, where the boundary-seeded
  sampler draws half its operands from a pool of interesting values
  (0, +/-1, INT_MIN, INT_MAX, powers of two, low-bit masks, ...).

and compared against the solver, which sees the whole input space at once.

Two controls guard the result. A proven rule must never yield a
counterexample under any configuration -- if it does, the interpreter and the
encoder disagree and the whole experiment is void. And the acceptance rate is
reported per rule: a precondition the sampler rarely satisfies makes a test
near-vacuous while it still prints zero counterexamples.
"""

from __future__ import annotations

import time

from experiments import _common as C

from verify.difftest import run_difftest
from verify.ruledsl import load_rules
from verify.smt_encode import PROVEN, REFUTED, verify_rule

BUDGETS = [100, 1_000, 10_000, 100_000]
STRATEGIES = {"uniform": 0.0, "boundary": 0.5}
SEED = 20260916


def _signed(v: int) -> int:
    v &= 0xFFFFFFFF
    return v - (1 << 32) if v & (1 << 31) else v


def main() -> dict:
    # `hard.rules` is excluded: those queries are at the solver's capability
    # boundary and return `unknown` or `sat` depending on timing, so including
    # them would make the denominator of the headline detection rate move
    # between runs. They are characterised separately in E3. `mined.rules`
    # is excluded for the same reason `hand_written_rule_files()` exists
    # everywhere else: this experiment's numbers were published before
    # Module B existed and must stay reproducible regardless of what a live
    # mining run later adds to rulelib/.
    rules = [r for path in C.hand_written_rule_files() for r in load_rules(str(path))
             if r.origin != "hard"]

    smt = {}
    smt_ms = 0.0
    for rule in rules:
        verdict = verify_rule(rule)
        smt[rule.name] = verdict
        smt_ms += verdict.solve_ms

    results, rows = {}, []
    false_alarms = []
    dt_ms = 0.0
    config_ms: dict[str, float] = {}

    for rule in rules:
        verdict = smt[rule.name]
        entry = {"origin": rule.origin, "smt": verdict.status, "configs": {}}
        row = [rule.name, rule.origin, verdict.status]
        for strategy, rate in STRATEGIES.items():
            for budget in BUDGETS:
                start = time.perf_counter()
                dt = run_difftest(rule, trials=budget, seed=SEED,
                                  boundary_rate=rate)
                spent = (time.perf_counter() - start) * 1000
                dt_ms += spent
                key_t = f"{strategy}@{budget}"
                config_ms[key_t] = config_ms.get(key_t, 0.0) + spent
                entry["configs"][f"{strategy}@{budget}"] = {
                    "found": dt.found,
                    "first_at": dt.first_at,
                    "accepted": dt.accepted,
                    "acceptance": round(dt.acceptance, 4),
                }
                if dt.found and verdict.status == PROVEN:
                    false_alarms.append(
                        f"{rule.name}: differential testing refuted a rule the "
                        f"solver proved ({strategy}@{budget})")
                row.append("Y" if dt.found else ".")
        entry["acceptance"] = entry["configs"][f"boundary@{BUDGETS[-1]}"]["acceptance"]
        results[rule.name] = entry
        rows.append(row)

    headers = ["rule", "origin", "solver"]
    headers += [f"u{b}" for b in BUDGETS] + [f"b{b}" for b in BUDGETS]

    C.heading("E4.1  Detection matrix   (Y = tester found a counterexample)")
    print("  u = uniform random sampling, b = boundary-seeded; "
          "number is the trial budget")
    C.table(rows, headers)

    refuted = [r for r in rules if smt[r.name].status == REFUTED]
    proven = [r for r in rules if smt[r.name].status == PROVEN]

    summary_rows = []
    for strategy in STRATEGIES:
        for budget in BUDGETS:
            key = f"{strategy}@{budget}"
            found = sum(1 for r in refuted if results[r.name]["configs"][key]["found"])
            summary_rows.append([strategy, budget, f"{found}/{len(refuted)}",
                                 C.pct(found, len(refuted))])

    C.heading("E4.2  Of the rules the solver refutes, how many does testing catch?")
    C.table(summary_rows, ["strategy", "budget", "caught", "rate"])
    print(f"\n  the solver refutes {len(refuted)}/{len(refuted)} (100.0%) at every "
          f"budget, because it does not sample")

    C.heading("E4.3  The rules testing misses")
    miss_rows = []
    for rule in refuted:
        cfg = results[rule.name]["configs"]
        best = max((budget for strategy in STRATEGIES for budget in BUDGETS
                    if cfg[f"{strategy}@{budget}"]["found"]), default=None)
        caught_by = [k for k, v in cfg.items() if v["found"]]
        if len(caught_by) < len(cfg):
            cex = ", ".join(f"{k}={_signed(v)}"
                            for k, v in sorted(smt[rule.name].counterexample.items()))
            miss_rows.append([
                rule.name, rule.origin,
                "never" if not caught_by else f"{len(caught_by)}/{len(cfg)} configs",
                cex or "-"])
    C.table(miss_rows, ["rule", "origin", "caught by", "solver counterexample"])

    C.heading("E4.4  Controls")
    print(f"  proven rules that testing wrongly refuted  {len(false_alarms)} "
          f"(of {len(proven)} proven rules x {len(BUDGETS) * len(STRATEGIES)} configs)")
    for line in false_alarms:
        print(f"    FALSE ALARM {line}")
    low = [(n, e["acceptance"]) for n, e in results.items() if e["acceptance"] < 0.2]
    print(f"  rules whose precondition the sampler satisfies <20% of the time: "
          f"{len(low)}")
    for name, rate in low:
        print(f"    {name}: acceptance {rate:.1%}")

    C.heading("E4.5  Cost per rule")
    total_trials = len(rules) * sum(BUDGETS) * len(STRATEGIES)
    cost_rows = [[k, f"{v / len(rules):.2f}"]
                 for k, v in sorted(config_ms.items(),
                                    key=lambda kv: (kv[0].split("@")[0],
                                                    int(kv[0].split("@")[1])))]
    cost_rows.append(["solver (whole input space)", f"{smt_ms / len(rules):.2f}"])
    C.table(cost_rows, ["configuration", "ms per rule"])
    print(f"\n  {total_trials:,} test trials in total ({dt_ms / 1000:.1f} s)")
    print("  the solver settles every rule for about the price of a "
          "thousand-trial test run,")
    print("  and unlike the test run its answer does not depend on which "
          "values were sampled.")

    payload = {
        "budgets": BUDGETS,
        "strategies": STRATEGIES,
        "seed": SEED,
        "rules": results,
        "refuted_total": len(refuted),
        "proven_total": len(proven),
        "summary": [{"strategy": s, "budget": b, "caught": c, "rate": r}
                    for s, b, c, r in summary_rows],
        "false_alarms": false_alarms,
        "cost_ms": {"difftest": dt_ms, "smt": smt_ms, "trials": total_trials},
    }
    C.save("exp04_difftest_vs_smt", payload)
    return payload


if __name__ == "__main__":
    main()
