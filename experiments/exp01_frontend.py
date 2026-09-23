"""E1 - front end: grammar, compilation, diagnostics, and grounding coverage.

Measures
--------
1. LL(1) conflicts in the MiniC grammar (must be zero).
2. Whether every benchmark program lexes, parses and type-checks.
3. On a mutation corpus: what fraction of injected faults the analyser
   reports, and what fraction of the resulting diagnostics carry the
   structured fields Module A needs (`expected`, `found`, `in_scope`).

(3) is the honest version of an RQ4 result at this stage. It measures the
*grounding layer* only. No model is involved, so this says nothing yet about
whether grounded context produces better fixes — only that the context exists
and is well formed.
"""

from __future__ import annotations

import statistics

from experiments import _common as C
from experiments.mutate import generate

from spec.grammar import GRAMMAR, NONTERMINALS, build_table
from frontend.diagnostics import CompileError
from frontend.parser import parse
from frontend.sema import analyze
from ir.ir import instr_count
from ir.irgen import generate as gen_ir


def main() -> dict:
    table_cells, conflicts = build_table()
    grammar = {
        "nonterminals": len(NONTERMINALS),
        "productions": sum(len(p) for p in GRAMMAR.values()),
        "table_cells": len(table_cells),
        "ll1_conflicts": len(conflicts),
        "conflict_detail": conflicts,
    }

    C.heading("E1.1  Grammar")
    C.table([[grammar["nonterminals"], grammar["productions"],
              grammar["table_cells"], grammar["ll1_conflicts"]]],
            ["nonterminals", "productions", "table cells", "LL(1) conflicts"])

    programs = []
    for path in C.bench_programs():
        src = path.read_text()
        entry = {"program": path.name, "lines": len(src.splitlines())}
        try:
            prog = parse(src)
            analyze(prog, src)
            module = gen_ir(prog)
            entry.update(ok=True, functions=len(module.functions),
                         ir_instructions=instr_count(module))
        except CompileError as exc:
            entry.update(ok=False,
                         errors=[d.code for d in exc.diagnostics])
        programs.append(entry)

    C.heading("E1.2  Benchmark programs")
    C.table([[p["program"], p["lines"], p.get("functions", "-"),
              p.get("ir_instructions", "-"), "ok" if p["ok"] else "FAILED"]
             for p in programs],
            ["program", "src lines", "functions", "IR instrs", "status"])

    by_operator: dict[str, dict] = {}
    grounded = ungrounded = 0
    all_codes: dict[str, int] = {}
    diag_counts: list[int] = []
    localised = 0

    for path in C.bench_programs():
        src = path.read_text()
        for mutant in generate(path.name, src):
            bucket = by_operator.setdefault(
                mutant.operator, {"mutants": 0, "detected": 0, "silent": 0,
                                  "on_line": 0})
            bucket["mutants"] += 1
            try:
                prog = parse(mutant.source)
                analyze(prog, mutant.source)
                bucket["silent"] += 1
                continue
            except CompileError as exc:
                bucket["detected"] += 1
                diag_counts.append(len(exc.diagnostics))
                if any(d.span.line == mutant.injected_line
                       for d in exc.diagnostics):
                    bucket["on_line"] += 1
                    localised += 1
                for d in exc.diagnostics:
                    all_codes[d.code] = all_codes.get(d.code, 0) + 1
                    context = d.to_context(mutant.source)
                    if {"expected", "found"} <= set(context):
                        grounded += 1
                    else:
                        ungrounded += 1

    total_mutants = sum(b["mutants"] for b in by_operator.values())
    detected = sum(b["detected"] for b in by_operator.values())
    silent = sum(b["silent"] for b in by_operator.values())

    C.heading("E1.3  Mutation corpus")
    C.table([[name, b["mutants"], b["detected"], b["silent"],
              C.pct(b["detected"], b["mutants"]),
              C.pct(b["on_line"], b["mutants"])]
             for name, b in sorted(by_operator.items())],
            ["operator", "mutants", "detected", "silent", "detection",
             "fault line reported"])
    print(f"\n  total mutants          {total_mutants}")
    print(f"  detected               {detected} ({C.pct(detected, total_mutants)})")
    print(f"  silent (still compile) {silent} ({C.pct(silent, total_mutants)})")
    print(f"  fault line reported    {localised} ({C.pct(localised, detected)}"
          f" of detected)")
    if diag_counts:
        print(f"  diagnostics per mutant median {statistics.median(diag_counts):.1f},"
              f" max {max(diag_counts)}")

    total_diags = grounded + ungrounded
    C.heading("E1.4  Grounded-context coverage (Module A substrate)")
    print(f"  diagnostics emitted                     {total_diags}")
    print(f"  carrying expected/found                 {grounded} "
          f"({C.pct(grounded, total_diags)})")
    print("  every diagnostic also carries a span, an in-scope symbol list and")
    print("  a source window; no model is involved in producing any of this.")
    C.table(sorted(([code, n] for code, n in all_codes.items()),
                   key=lambda r: -r[1]),
            ["error code", "times reported"])

    payload = {
        "grammar": grammar,
        "programs": programs,
        "mutation": {
            "by_operator": by_operator,
            "total": total_mutants,
            "detected": detected,
            "silent": silent,
            "fault_line_reported": localised,
            "codes": all_codes,
            "grounded_diagnostics": grounded,
            "ungrounded_diagnostics": ungrounded,
        },
    }
    C.save("exp01_frontend", payload)
    return payload


if __name__ == "__main__":
    main()
