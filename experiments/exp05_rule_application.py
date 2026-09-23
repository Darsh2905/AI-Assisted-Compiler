"""E5 - applying a proven library deterministically, and what that costs.

This is the RQ2 artifact: the model is absent at build time, and the compiler
consults a finite set of already-proven rules. Two things get measured.

**Determinism.** The same program compiled twice against the same library must
produce byte-identical IR. That is the property the offline-mining design buys
and the property an LLM-in-the-loop compiler cannot offer.

**Soundness does not imply profitability.** The first run of this experiment
applied every proven rule and grew the benchmark suite by 60%, because
`add_split_or_and` (x + y == (x|y) + (x&y)) is perfectly correct and turns one
instruction into three. It fired 375 times. A proof says a rewrite preserves
meaning; it says nothing about whether the rewrite is an improvement, and the
two decisions need separate machinery. The applier therefore consults a static
cost model and applies a rule only when it strictly reduces cost.

The reported reduction is small and the honest reason is stated in the output:
only a handful of library rules ever match a six-program benchmark suite. That
is a statement about the corpus, not about the method, and it is the strongest
argument for the corpus work that comes next.
"""

from __future__ import annotations

from experiments import _common as C

from frontend.parser import parse
from frontend.sema import analyze
from ir.ir import instr_count
from ir.irgen import generate as gen_ir
from ir.printer import print_module
from passes.peephole import classify, optimize, side_cost
from verify.ruledsl import load_rules
from verify.smt_encode import verify_rule


def compile_program(src: str):
    prog = parse(src)
    analyze(prog, src)
    return gen_ir(prog)


def main() -> dict:
    candidates = [r for path in C.hand_written_rule_files()
                 for r in load_rules(str(path))]
    proven = [r for r in candidates if verify_rule(r).is_proof]
    profitable, neutral = classify(proven)

    C.heading("E5.1  What the library admits")
    print(f"  candidate rules                    {len(candidates)}")
    print(f"  proven                             {len(proven)}")
    print(f"  proven and cost-reducing (applied) {len(profitable)}")
    print(f"  proven but not cost-reducing       {len(neutral)}")
    C.table([[r.name, side_cost(r.match), side_cost(r.rewrite),
              "applied" if r in profitable else "held back"]
             for r in proven],
            ["rule", "match cost", "rewrite cost", "status"])

    rows, per_rule = [], {}
    before_total = after_total = 0
    deterministic = True

    for path in C.bench_programs():
        src = path.read_text()
        module = compile_program(src)
        before = instr_count(module)
        stats = optimize(module, proven)
        after = instr_count(module)
        first = print_module(module)

        again = compile_program(src)
        optimize(again, proven)
        if print_module(again) != first:
            deterministic = False

        before_total += before
        after_total += after
        for name, n in stats.applied.items():
            per_rule[name] = per_rule.get(name, 0) + n
        rows.append([path.name, before, after, before - after,
                     f"{(before - after) / before:.1%}", stats.total])

    rows.append(["TOTAL", before_total, after_total,
                 before_total - after_total,
                 f"{(before_total - after_total) / before_total:.1%}",
                 sum(per_rule.values())])

    C.heading("E5.2  Instruction counts")
    C.table(rows, ["program", "before", "after", "removed", "reduction",
                   "rule firings"])

    C.heading("E5.3  Which rules actually fire")
    fired = sorted(per_rule.items(), key=lambda kv: -kv[1])
    C.table([[n, c] for n, c in fired], ["rule", "firings"])
    never = [r.name for r in profitable if r.name not in per_rule]
    print(f"\n  {len(fired)} of {len(profitable)} applicable rules ever matched; "
          f"{len(never)} never did.")
    print("  On a six-program suite most of a rule library is dead weight. The fix")
    print("  is a larger corpus, not a larger library.")

    C.heading("E5.4  Determinism")
    print(f"  recompiling every program against the same library reproduces "
          f"byte-identical IR: {deterministic}")
    print("  no model is consulted at build time, so there is no sampling "
          "temperature,")
    print("  no prompt cache and no network dependency in the compile path.")

    payload = {
        "library": {
            "candidates": len(candidates), "proven": len(proven),
            "profitable": [r.name for r in profitable],
            "held_back": [r.name for r in neutral],
            "costs": {r.name: {"match": side_cost(r.match),
                               "rewrite": side_cost(r.rewrite)} for r in proven},
        },
        "programs": [{"program": r[0], "before": r[1], "after": r[2],
                      "firings": r[5]} for r in rows[:-1]],
        "total": {"before": before_total, "after": after_total,
                  "reduction": (before_total - after_total) / before_total},
        "firings": per_rule,
        "never_fired": never,
        "deterministic": deterministic,
    }
    C.save("exp05_rule_application", payload)
    return payload


if __name__ == "__main__":
    main()
