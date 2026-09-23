"""Tests for the deterministic rule applier.

The applier is the part a build actually runs, so the properties asserted here
are the ones a user would notice: it must not make code worse, it must not
change meaning, and it must produce the same output twice.
"""

from __future__ import annotations

import glob

import pytest

from frontend.parser import parse
from frontend.sema import analyze
from ir.ir import instr_count
from ir.irgen import generate as gen_ir
from ir.printer import print_module
from passes.peephole import (apply_rules, classify, constant_fold, dce,
                             is_profitable, module_cost, optimize, side_cost)
from verify.ruledsl import load_rules
from verify.smt_encode import verify_rule

BENCH = sorted(glob.glob("bench/*.mc"))
PROVEN = [r for r in load_rules("rulelib/textbook.rules") if verify_rule(r).is_proof]


def compile_to_ir(path: str):
    src = open(path).read()
    prog = parse(src)
    analyze(prog, src)
    return gen_ir(prog)


def test_only_proven_rules_reach_the_applier():
    assert len(PROVEN) == len(load_rules("rulelib/textbook.rules")), (
        "every rule in textbook.rules is supposed to be provable")


def test_profitable_rules_strictly_reduce_cost():
    profitable, held = classify(PROVEN)
    assert profitable, "no rule is cost-reducing; the applier would be a no-op"
    for rule in profitable:
        assert side_cost(rule.rewrite) < side_cost(rule.match)
    for rule in held:
        assert side_cost(rule.rewrite) >= side_cost(rule.match)


def test_no_pair_of_applied_rules_can_oscillate():
    """Strict cost reduction makes a rewrite cycle impossible."""
    profitable, _ = classify(PROVEN)
    for rule in profitable:
        assert side_cost(rule.rewrite) < side_cost(rule.match)


@pytest.mark.parametrize("path", BENCH)
def test_optimisation_never_increases_cost(path):
    """Cost, not instruction count, is what the applier optimises.

    `mul_three_to_shift_add` trades one multiply for a shift and an add: the
    instruction count goes up by one and the cost goes down by two.
    """
    module = compile_to_ir(path)
    before = module_cost(module)
    optimize(module, PROVEN)
    assert module_cost(module) <= before, (
        f"{path}: the pipeline raised static cost from {before} to "
        f"{module_cost(module)}")


@pytest.mark.parametrize("path", BENCH)
def test_compilation_is_deterministic(path):
    first = compile_to_ir(path)
    optimize(first, PROVEN)
    second = compile_to_ir(path)
    optimize(second, PROVEN)
    assert print_module(first) == print_module(second)


@pytest.mark.parametrize("path", BENCH)
def test_optimised_ir_still_roundtrips(path):
    from ir.irparser import parse_module
    module = compile_to_ir(path)
    optimize(module, PROVEN)
    text = print_module(module)
    assert print_module(parse_module(text)) == text


@pytest.mark.parametrize("path", BENCH)
def test_every_block_still_terminates_after_optimisation(path):
    module = compile_to_ir(path)
    optimize(module, PROVEN)
    for fn in module.functions:
        for block in fn.blocks:
            assert block.terminator is not None


def test_unprofitable_rules_are_held_back_by_default():
    """With the cost model off, the library inflates the program.

    This is the regression guard for the bug this experiment actually found:
    `add_split_or_and` is proven, and applying every proven rule grew the suite
    by roughly 60%.
    """
    unguarded_total = guarded_total = baseline_total = 0
    for path in BENCH:
        baseline = compile_to_ir(path)
        baseline_total += module_cost(baseline)

        loose = compile_to_ir(path)
        apply_rules(loose, PROVEN, only_profitable=False)
        unguarded_total += module_cost(loose)

        tight = compile_to_ir(path)
        apply_rules(tight, PROVEN, only_profitable=True)
        guarded_total += module_cost(tight)

    assert unguarded_total > baseline_total, (
        "applying every proven rule is expected to inflate the program; if it "
        "no longer does, the cost-model guard is no longer being exercised")
    assert guarded_total < baseline_total, (
        "with the guard on, applying the library must strictly improve the "
        "suite")


def test_constant_folding_leaves_traps_alone():
    from ir.irparser import parse_module
    module = parse_module(
        "func @f() -> i32 {\n"
        "^entry:\n"
        "  %0 = const i32 1\n"
        "  %1 = const i32 0\n"
        "  %2 = sdiv %0, %1\n"
        "  ret %2\n"
        "}\n")
    constant_fold(module)
    ops = [i.op for i in module.functions[0].instructions()]
    assert "sdiv" in ops, "folding 1/0 would replace an observable trap with a value"


def test_constant_folding_does_fold_ordinary_arithmetic():
    from ir.irparser import parse_module
    module = parse_module(
        "func @f() -> i32 {\n"
        "^entry:\n"
        "  %0 = const i32 6\n"
        "  %1 = const i32 7\n"
        "  %2 = mul %0, %1\n"
        "  ret %2\n"
        "}\n")
    assert constant_fold(module) == 1
    dce(module)
    folded = [i for i in module.functions[0].instructions()
              if i.op == "const" and i.args[0].value == 42]
    assert folded, "6 * 7 should have been folded to 42"
