"""Regenerate every number this project reports.

    python3 -m experiments.run_all

Writes `results/*.json` and prints the tables. Nothing in the README, the
status page or the paper is typed in by hand; if a number there is not
reproduced by this script, it does not belong there.
"""

from __future__ import annotations

import time

from experiments import _common as C
from experiments import (exp01_frontend, exp02_ir_roundtrip,
                         exp03_rule_verification, exp04_difftest_vs_smt,
                         exp05_rule_application)

EXPERIMENTS = [
    ("E1  front end, diagnostics, mutation corpus", exp01_frontend),
    ("E2  IR textual round trip", exp02_ir_roundtrip),
    ("E3  rule verification", exp03_rule_verification),
    ("E4  differential testing vs the solver", exp04_difftest_vs_smt),
    ("E5  deterministic rule application", exp05_rule_application),
]


def main() -> None:
    started = time.time()
    summary = {}
    for title, module in EXPERIMENTS:
        print("\n" + "#" * 72)
        print("# " + title)
        print("#" * 72)
        summary[module.__name__.split(".")[-1]] = module.main()

    C.heading("Headline numbers")
    e1, e3 = summary["exp01_frontend"], summary["exp03_rule_verification"]
    e4, e5 = summary["exp04_difftest_vs_smt"], summary["exp05_rule_application"]

    def best(strategy: str) -> int:
        return max(int(r["caught"].split("/")[0]) for r in e4["summary"]
                   if r["strategy"] == strategy)

    uniform_best, boundary_best = best("uniform"), best("boundary")

    rows = [
        ["LL(1) conflicts in the MiniC grammar", e1["grammar"]["ll1_conflicts"]],
        ["benchmark programs compiled", len(e1["programs"])],
        ["mutants detected",
         f'{e1["mutation"]["detected"]}/{e1["mutation"]["total"]}'],
        ["rules checked", e3["total"]],
        ["proven", e3["status_counts"].get("PROVEN", 0)],
        ["refuted", e3["status_counts"].get("REFUTED", 0)],
        ["counterexamples reproduced by the reference interpreter",
         f'{e3["replay"]["confirmed"]}/{e3["replay"]["produced"]}'],
        ["median solve time",
         f'{e3["solve_ms"].get("median", 0):.1f} ms'],
        ["wrong rules caught by uniform random testing (best budget)",
         f'{uniform_best} of {e4["refuted_total"]}'],
        ["wrong rules caught by boundary-seeded testing (best budget)",
         f'{boundary_best} of {e4["refuted_total"]}'],
        ["wrong rules caught by the solver",
         f'{e4["refuted_total"]} of {e4["refuted_total"]}'],
        ["proven rules wrongly refuted by testing", len(e4["false_alarms"])],
        ["library rules that are cost-reducing",
         f'{len(e5["library"]["profitable"])} of {e5["library"]["proven"]}'],
        ["rules that ever matched the benchmark suite",
         f'{len(e5["firings"])} of {len(e5["library"]["profitable"])}'],
        ["compilation is byte-identical across runs", e5["deterministic"]],
    ]
    C.table(rows, ["measurement", "value"])

    C.save("summary", {"headline": {r[0]: r[1] for r in rows},
                       "generated_seconds": round(time.time() - started, 1)})
    print(f"\nregenerated in {time.time() - started:.0f}s; "
          f"results written to {C.RESULTS}")


if __name__ == "__main__":
    main()
