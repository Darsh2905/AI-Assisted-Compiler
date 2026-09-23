"""Tests for the grammar, lexer, parser, semantic analyser and IR."""

from __future__ import annotations

import glob
import random

import pytest

from experiments.exp02_ir_roundtrip import random_module
from frontend.diagnostics import CompileError
from frontend.lexer import tokenize
from frontend.parser import parse
from frontend.sema import analyze
from ir.ir import Const, Instr, Module, Reg, instr_count
from ir.irgen import generate as gen_ir
from ir.irparser import parse_module
from ir.printer import print_module
from passes.peephole import dce
from spec.grammar import EPSILON, build_table, compute_first, compute_follow

BENCH = sorted(glob.glob("bench/*.mc"))


# ---------------------------------------------------------------- grammar --

def test_grammar_is_ll1_with_no_conflicts():
    _table, conflicts = build_table()
    assert conflicts == [], (
        "the MiniC grammar must be conflict-free LL(1); mandatory braces on "
        "if/while/for bodies is what removes the dangling-else conflict, so a "
        "conflict here means that decision was undone:\n  "
        + "\n  ".join(conflicts))


def test_grammar_has_no_unreachable_nonterminal():
    first = compute_first()
    empty = [nt for nt, f in first.items() if not f]
    assert empty == [], f"nonterminals deriving nothing: {empty}"


def test_every_nonterminal_has_a_follow_set():
    first = compute_first()
    follow = compute_follow(first)
    assert all(follow[nt] for nt in follow), "an unreachable nonterminal exists"
    assert EPSILON not in {t for s in follow.values() for t in s}


# ------------------------------------------------------------------ lexer --

def test_spans_cover_the_token_text():
    src = "int main() {\n  return 42;\n}\n"
    toks = tokenize(src)
    lines = src.splitlines()
    for tok in toks:
        if tok.kind == "eof":
            continue
        line = lines[tok.span.line - 1]
        assert line[tok.span.col_start - 1:tok.span.col_end - 1] == tok.text, (
            f"span of {tok} does not select its own text")


def test_int_min_literal_is_accepted_and_int_max_plus_one_is_not():
    prog = parse("int f() { return -2147483648; }")
    assert prog is not None
    with pytest.raises(CompileError) as exc:
        src = "int f() { return 2147483648; }"
        analyze(parse(src), src)
    assert exc.value.diagnostics[0].code == "E0204"


def test_abbreviation_like_tokens_do_not_break_comments():
    src = "int f() { /* a /* nested-looking */ return 1; }"
    assert parse(src) is not None


# ----------------------------------------------------------------- parser --

@pytest.mark.parametrize("path", BENCH)
def test_benchmarks_parse_and_typecheck(path):
    src = open(path).read()
    prog = parse(src)
    analyze(prog, src)
    assert prog.functions, f"{path} produced no functions"


def test_precedence_follows_c():
    """`a + b*2 < a<<1 && b>0` must group as `((a + (b*2)) < (a<<1)) && (b>0)`.

    Any other grouping is a type error, so type-checking it is the test: for
    example if `<` bound tighter than `<<` the left operand of `<<` would be a
    bool.
    """
    src = "bool f(int a, int b) { return a + b * 2 < a << 1 && b > 0; }"
    analyze(parse(src), src)


def test_else_if_chains_nest_correctly():
    src = """
    int f(int x) {
      if (x < 0) { return 0; }
      else if (x < 10) { return 1; }
      else { return 2; }
    }
    """
    analyze(parse(src), src)


# ------------------------------------------------------------------- sema --

@pytest.mark.parametrize("src,code", [
    ("int f() { return g(); }", "E0103"),
    ("int f() { return q; }", "E0101"),
    ("int f() { int x = true; return x; }", "E0201"),
    ("int f(int n) { if (n) { return 1; } return 0; }", "E0203"),
    ("int f() { return true; }", "E0402"),
    ("int f(int n) { if (n > 0) { return 1; } }", "E0401"),
    ("int f() { int x = 1; int x = 2; return x; }", "E0102"),
    ("int g(int a, int b) { return a; } int f() { return g(1); }", "E0301"),
    ("int f() { int a[4]; return a[true]; }", "E0501"),
    ("int f() { int x = 1; return x[0]; }", "E0502"),
    ("int f() { 1 = 2; return 0; }", "E0601"),
])
def test_each_error_class_is_detected(src, code):
    with pytest.raises(CompileError) as exc:
        analyze(parse(src), src)
    codes = [d.code for d in exc.value.diagnostics]
    assert code in codes, f"expected {code}, got {codes}"


def test_grounded_context_has_the_fields_module_a_needs():
    src = "int f(int count, bool done) {\n  int total = done;\n  return total;\n}"
    with pytest.raises(CompileError) as exc:
        analyze(parse(src), src)
    context = exc.value.diagnostics[0].to_context(src)
    assert context["expected"] == "int"
    assert context["found"] == "bool"
    assert context["span"]["line"] == 2
    names = {s["name"] for s in context["in_scope"]}
    assert {"count", "done"} <= names
    assert "source_window" in context


def test_multiple_faults_are_all_reported():
    src = """
    int f(bool b) {
      int a = b;
      bool c = 1;
      return q;
    }
    """
    with pytest.raises(CompileError) as exc:
        analyze(parse(src), src)
    assert len(exc.value.diagnostics) >= 3, (
        "the analyser must accumulate diagnostics rather than stop at the "
        "first; the mutation corpus depends on it")


# --------------------------------------------------------------------- IR --

@pytest.mark.parametrize("path", BENCH)
def test_ir_roundtrips_for_benchmarks(path):
    src = open(path).read()
    prog = parse(src)
    analyze(prog, src)
    text = print_module(gen_ir(prog))
    assert print_module(parse_module(text)) == text


def test_ir_roundtrips_for_random_modules():
    rng = random.Random(4242)
    for _ in range(120):
        text = print_module(random_module(rng))
        assert print_module(parse_module(text)) == text


def test_every_block_ends_in_a_terminator():
    for path in BENCH:
        src = open(path).read()
        prog = parse(src)
        analyze(prog, src)
        for fn in gen_ir(prog).functions:
            for block in fn.blocks:
                assert block.terminator is not None, (
                    f"{path}:{fn.name}:^{block.label} has no terminator")


def test_dce_keeps_trapping_instructions():
    """A dead `sdiv` still traps, so removing it would change behaviour."""
    module = parse_module(
        "func @f(i32 %a, i32 %b) -> i32 {\n"
        "^entry:\n"
        "  %0 = sdiv %a, %b\n"
        "  %1 = add %a, %a\n"
        "  %2 = const i32 7\n"
        "  ret %2\n"
        "}\n")
    dce(module)
    kept = {i.op for i in module.functions[0].instructions()}
    assert "sdiv" in kept, "DCE removed a trapping instruction"
    assert "add" not in kept, "DCE failed to remove a genuinely dead instruction"


def test_short_circuit_lowers_to_branches():
    src = "bool f(int a[4], int i) { return 0 <= i && a[i] > 0; }"
    prog = parse(src)
    analyze(prog, src)
    fn = gen_ir(prog).functions[0]
    assert any(b.label.startswith("sc.") for b in fn.blocks), (
        "`&&` must lower to control flow so its short-circuit trap semantics "
        "are visible in the IR, not just in the language reference")
    assert instr_count(Module([fn])) > 0
