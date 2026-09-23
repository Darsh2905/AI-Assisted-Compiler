"""E3 - the verifier proves what it should and rejects what it should.

This is the week 7-9 exit criterion from the project plan, made executable:
the checker has to prove hand-written correct rules and refute hand-written
wrong ones, and each rule file declares the verdict it expects so a regression
in the encoder fails here rather than downstream.

Two checks matter as much as the headline counts:

* **Cross-implementation agreement.** Every counterexample the solver reports
  is replayed through `verify/interp.py`, an implementation of the same
  specification written independently of the encoder. A counterexample the
  interpreter cannot reproduce means the two have diverged and neither can be
  trusted. This is the mitigation for "verifier bug gives a false proven" in
  the plan's risk table.
* **Vacuity.** A rule whose precondition is unsatisfiable proves trivially and
  can never fire. Counting those as proofs would inflate the library with dead
  entries, so they are reported as a separate status.
"""

from __future__ import annotations

import statistics

from experiments import _common as C

from passes.peephole import is_profitable, side_cost
from verify.difftest import replay
from verify.ruledsl import load_rules
from verify.smt_encode import PROVEN, REFUTED, verify_rule

TIMEOUT_MS = 10_000


def main() -> dict:
    by_file, rows = {}, []
    times, mismatches, unexpected = [], [], []
    replayed = confirmed = 0
    status_counts: dict[str, int] = {}

    for path in C.hand_written_rule_files():
        rules = load_rules(str(path))
        entries = []
        for rule in rules:
            verdict = verify_rule(rule, timeout_ms=TIMEOUT_MS)
            times.append(verdict.solve_ms)
            status_counts[verdict.status] = status_counts.get(verdict.status, 0) + 1

            # A rule declared `inconclusive` sits at the solver's capability
            # boundary and its verdict is timing-dependent, so the only thing
            # asserted is the safety half: it must never come back PROVEN.
            expected = (rule.expect or "").upper()
            if expected == "INCONCLUSIVE":
                if verdict.status == PROVEN:
                    unexpected.append(f"{rule.name}: an unsettled query was "
                                      f"reported PROVEN")
            elif expected and verdict.status != expected:
                unexpected.append(f"{rule.name}: expected {expected}, "
                                  f"got {verdict.status}")

            confirm = "-"
            if verdict.status == REFUTED:
                replayed += 1
                agree, why = replay(rule, verdict.counterexample)
                if agree:
                    mismatches.append(f"{rule.name}: solver reported a "
                                      f"counterexample the interpreter cannot "
                                      f"reproduce ({why})")
                    confirm = "DIVERGED"
                else:
                    confirmed += 1
                    confirm = "confirmed"

            entries.append({
                "name": rule.name,
                "origin": rule.origin,
                "expect": rule.expect,
                "status": verdict.status,
                "solve_ms": round(verdict.solve_ms, 2),
                "match_cost": side_cost(rule.match),
                "rewrite_cost": side_cost(rule.rewrite),
                "profitable": is_profitable(rule),
                "counterexample": verdict.counterexample,
                "detail": verdict.detail,
            })
            rows.append([rule.name, rule.origin, verdict.status,
                         f"{verdict.solve_ms:.1f}", confirm,
                         "yes" if is_profitable(rule) else "no"])
        by_file[path.name] = entries

    C.heading("E3.1  Every rule, every verdict")
    C.table(rows, ["rule", "origin", "verdict", "solve ms", "replay",
                   "cost-reducing"])

    total = sum(len(v) for v in by_file.values())
    proven = status_counts.get(PROVEN, 0)
    refuted = status_counts.get(REFUTED, 0)

    C.heading("E3.2  Summary")
    print(f"  rules checked                  {total}")
    print(f"  proven                         {proven}")
    print(f"  refuted                        {refuted}")
    for status, n in sorted(status_counts.items()):
        if status not in {PROVEN, REFUTED}:
            print(f"  {status.lower():30s} {n}")
    print(f"  verdicts matching declaration  {total - len(unexpected)}/{total}")
    for line in unexpected:
        print(f"    MISMATCH {line}")

    C.heading("E3.3  Cross-implementation agreement")
    print(f"  counterexamples produced by the solver   {replayed}")
    print(f"  reproduced by the reference interpreter  {confirmed} "
          f"({C.pct(confirmed, replayed)})")
    for line in mismatches:
        print(f"    DIVERGENCE {line}")

    C.heading("E3.4  Solver cost")
    if times:
        print(f"  min {min(times):.1f} ms   median {statistics.median(times):.1f} ms"
              f"   max {max(times):.1f} ms   total {sum(times):.0f} ms")
        print(f"  timeout budget {TIMEOUT_MS} ms; "
              f"{status_counts.get('INCONCLUSIVE', 0)} rule(s) hit it")

    payload = {
        "by_file": by_file,
        "total": total,
        "status_counts": status_counts,
        "expectation_mismatches": unexpected,
        "replay": {"produced": replayed, "confirmed": confirmed,
                   "divergences": mismatches},
        "solve_ms": {"min": min(times), "median": statistics.median(times),
                     "max": max(times), "total": sum(times)} if times else {},
        "timeout_ms": TIMEOUT_MS,
    }
    C.save("exp03_rule_verification", payload)
    return payload


if __name__ == "__main__":
    main()
