#!/usr/bin/env python3
"""ProveIt-C command line.

    ./provitc.py tokens   FILE.mc          token stream with spans
    ./provitc.py ast      FILE.mc          parse tree
    ./provitc.py check    FILE.mc          type-check only
    ./provitc.py context  FILE.mc          diagnostics as Module A grounded JSON
    ./provitc.py ir       FILE.mc [-O]     emit IR, optionally optimised
    ./provitc.py verify   [RULES.rules]    verify a rule file
    ./provitc.py library                   show the proven, applicable library
"""

from __future__ import annotations

import argparse
import glob
import json
import sys

from frontend import ast_nodes as A
from frontend.diagnostics import CompileError
from frontend.lexer import tokenize
from frontend.parser import parse
from frontend.sema import analyze
from ir.ir import instr_count
from ir.irgen import generate
from ir.printer import print_module
from passes.peephole import classify, is_profitable, optimize, side_cost
from verify.difftest import run_difftest
from verify.ruledsl import load_rules
from verify.smt_encode import verify_rule


def read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def front_end(path: str):
    src = read(path)
    prog = parse(src)
    analyze(prog, src)
    return src, prog


def show_diagnostics(path: str, exc: CompileError, as_json: bool = False) -> int:
    src = read(path)
    if as_json:
        print(json.dumps([d.to_context(src) for d in exc.diagnostics], indent=2))
    else:
        for d in exc.diagnostics:
            print(d.render(src, path), file=sys.stderr)
        print(f"\n{len(exc.diagnostics)} error(s)", file=sys.stderr)
    return 1


def ast_lines(node, indent: int = 0) -> list[str]:
    """Render an AST subtree as indented lines.

    Used by both the CLI (`dump_ast` prints these) and the local web front end
    (`web/server.py` returns them as one string), so the tree shown in a
    browser is the same text the terminal prints, not a second rendering that
    could drift from it.
    """
    pad = "  " * indent
    out: list[str] = []
    if isinstance(node, A.Program):
        out.append(f"{pad}Program")
        for fn in node.functions:
            out += ast_lines(fn, indent + 1)
    elif isinstance(node, A.FuncDecl):
        params = ", ".join(f"{p.declared_type} {p.name}" for p in node.params)
        out.append(f"{pad}FuncDecl {node.return_type} {node.name}({params})")
        out += ast_lines(node.body, indent + 1)
    elif isinstance(node, A.Block):
        out.append(f"{pad}Block")
        for s in node.stmts:
            out += ast_lines(s, indent + 1)
    elif isinstance(node, (A.If, A.While, A.For)):
        out.append(f"{pad}{type(node).__name__}")
        for attr in ("init", "cond", "step", "body", "then_body", "else_body"):
            child = getattr(node, attr, None)
            if child is not None:
                out.append(f"{pad}  .{attr}")
                out += ast_lines(child, indent + 2)
    elif isinstance(node, A.Expr):
        ty = f" : {node.ty}" if node.ty else ""
        if isinstance(node, A.Binary):
            out.append(f"{pad}Binary {node.op}{ty}")
            out += ast_lines(node.lhs, indent + 1)
            out += ast_lines(node.rhs, indent + 1)
        elif isinstance(node, A.Unary):
            out.append(f"{pad}Unary {node.op}{ty}")
            out += ast_lines(node.operand, indent + 1)
        elif isinstance(node, A.VarRef):
            out.append(f"{pad}VarRef {node.name}{ty}")
        elif isinstance(node, A.IntLit):
            out.append(f"{pad}IntLit {node.value}{ty}")
        elif isinstance(node, A.BoolLit):
            out.append(f"{pad}BoolLit {node.value}{ty}")
        elif isinstance(node, A.Call):
            out.append(f"{pad}Call{ty}")
            for a in node.args:
                out += ast_lines(a, indent + 1)
        elif isinstance(node, A.Index):
            out.append(f"{pad}Index{ty}")
            out += ast_lines(node.base, indent + 1)
            out += ast_lines(node.index, indent + 1)
        else:
            out.append(f"{pad}{type(node).__name__}{ty}")
    else:
        out.append(f"{pad}{type(node).__name__}")
        for attr in ("target", "value", "expr", "init"):
            child = getattr(node, attr, None)
            if isinstance(child, (A.Expr, A.Stmt)):
                out += ast_lines(child, indent + 1)
    return out


def dump_ast(node, indent: int = 0) -> None:
    for line in ast_lines(node, indent):
        print(line)


def proven_library() -> list:
    rules = [r for path in sorted(glob.glob("rulelib/*.rules"))
             for r in load_rules(path)]
    return [r for r in rules if verify_rule(r).is_proof]


def main() -> int:
    ap = argparse.ArgumentParser(prog="provitc", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["tokens", "ast", "check", "context",
                                        "ir", "verify", "library"])
    ap.add_argument("file", nargs="?")
    ap.add_argument("-O", "--optimize", action="store_true",
                    help="apply the proven rule library (ir only)")
    ap.add_argument("--difftest", type=int, default=0, metavar="N",
                    help="also run N differential-test trials (verify only)")
    args = ap.parse_args()

    if args.command == "library":
        proven = proven_library()
        profitable, held = classify(proven)
        print(f"{len(proven)} proven rules, {len(profitable)} cost-reducing\n")
        for rule in proven:
            mark = "apply" if is_profitable(rule) else "hold "
            print(f"  [{mark}] {rule.name:34s} "
                  f"cost {side_cost(rule.match):2d} -> {side_cost(rule.rewrite):2d}"
                  f"   {rule.note}")
        return 0

    if args.command == "verify":
        paths = [args.file] if args.file else sorted(glob.glob("rulelib/*.rules"))
        failures = 0
        for path in paths:
            print(f"== {path}")
            for rule in load_rules(path):
                verdict = verify_rule(rule)
                print(f"  {verdict}")
                if args.difftest:
                    print(f"    {run_difftest(rule, trials=args.difftest)}")
                if rule.expect and rule.expect != "inconclusive" \
                        and verdict.status != rule.expect.upper():
                    failures += 1
        if failures:
            print(f"\n{failures} rule(s) did not match their declared verdict",
                  file=sys.stderr)
        return 1 if failures else 0

    if not args.file:
        ap.error(f"'{args.command}' needs a source file")

    if args.command == "tokens":
        for tok in tokenize(read(args.file)):
            print(f"  {tok}")
        return 0

    try:
        src, prog = front_end(args.file)
    except CompileError as exc:
        return show_diagnostics(args.file, exc, as_json=(args.command == "context"))

    if args.command == "check":
        print(f"{args.file}: ok ({len(prog.functions)} function(s))")
        return 0
    if args.command == "context":
        print("[]")
        return 0
    if args.command == "ast":
        dump_ast(prog)
        return 0

    module = generate(prog)
    before = instr_count(module)
    if args.optimize:
        stats = optimize(module, proven_library())
        print(f"; {before} -> {instr_count(module)} instructions, "
              f"{stats.total} rule application(s): "
              f"{stats.applied or 'none matched'}")
    print(print_module(module), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
