"""Tests for the verification core.

Each test asserts a numeric threshold or an exact verdict. None of them passes
merely because a script ran without raising: a suite that only checks
well-formedness is the same mistake this project exists to argue against.
"""

from __future__ import annotations

import random

import pytest

from passes.peephole import is_profitable
from verify.difftest import replay, run_difftest
from verify.interp import eval_side
from verify.ruledsl import (RuleSyntaxError, RuleTypeError, load_rules,
                            parse_rules)
from verify.smt_encode import (INCONCLUSIVE, PROVEN, REFUTED, VACUOUS,
                               evaluate_concrete, verify_rule)

TEXTBOOK = load_rules("rulelib/textbook.rules")
ADVERSARIAL = load_rules("rulelib/adversarial.rules")
STEALTH = load_rules("rulelib/stealth.rules")
HARD = load_rules("rulelib/hard.rules")
#: Rules with a stable expected verdict. `hard.rules` is excluded on purpose:
#: its verdicts are timing-dependent, so only the safety property is asserted
#: (see test_unsettled_rules_are_never_proven).
ALL_RULES = TEXTBOOK + ADVERSARIAL + STEALTH
EVERY_RULE = ALL_RULES + HARD


def test_library_is_not_empty():
    assert len(TEXTBOOK) >= 15, "the proven control group must be substantial"
    assert len(ADVERSARIAL) >= 8, "a checker that never rejects proves nothing"
    assert len(STEALTH) >= 3


@pytest.mark.parametrize("rule", ALL_RULES, ids=lambda r: r.name)
def test_rule_matches_its_declared_verdict(rule):
    verdict = verify_rule(rule)
    assert rule.expect is not None, f"{rule.name} declares no expected verdict"
    assert verdict.status == rule.expect.upper(), (
        f"{rule.name}: expected {rule.expect.upper()}, got {verdict.status} "
        f"({verdict.detail})")


@pytest.mark.parametrize("rule", ADVERSARIAL + STEALTH, ids=lambda r: r.name)
def test_counterexamples_are_reproducible(rule):
    """Every refutation must be confirmed by the independent interpreter."""
    verdict = verify_rule(rule)
    assert verdict.status == REFUTED
    agree, why = replay(rule, verdict.counterexample)
    assert not agree, (
        f"{rule.name}: the solver reported a counterexample that the reference "
        f"interpreter cannot reproduce ({why}); the encoder and the "
        f"specification have diverged")


@pytest.mark.parametrize("rule", TEXTBOOK, ids=lambda r: r.name)
def test_proven_rules_survive_differential_testing(rule):
    """A proven rule must never yield a counterexample under testing."""
    result = run_difftest(rule, trials=2_000, seed=7)
    assert not result.found, (
        f"{rule.name} was proven but differential testing refuted it: "
        f"{result.counterexamples[:1]}")


@pytest.mark.parametrize("rule", ALL_RULES, ids=lambda r: r.name)
def test_encoder_and_interpreter_agree_pointwise(rule):
    """The SMT encoding and the reference interpreter are two readings of
    spec/semantics.md; they must produce the same value and the same trap flag
    on the same concrete input."""
    rng = random.Random(f"agree:{rule.name}")
    names = rule.free_vars + rule.const_syms
    for _ in range(40):
        env = {n: rng.getrandbits(32) for n in names}
        for name in rule.free_vars:
            if rule.types.get(name) == "i1":
                env[name] = rng.getrandbits(1)
        for side, instrs in (("match", rule.match), ("rewrite", rule.rewrite)):
            want_value, want_trap = eval_side(instrs, env)
            got_value, got_trap = evaluate_concrete(rule, side, env)
            assert want_trap == got_trap, (
                f"{rule.name} [{side}] env={env}: interpreter trap={want_trap}, "
                f"encoder trap={got_trap}")
            if not want_trap:
                assert want_value == got_value, (
                    f"{rule.name} [{side}] env={env}: interpreter={want_value}, "
                    f"encoder={got_value}")


def test_trap_preservation_is_enforced():
    """A rewrite that is value-correct but erases a trap must be refuted."""
    rule = next(r for r in ADVERSARIAL if r.name == "shl_mask_trap_erasure")
    verdict = verify_rule(rule)
    assert verdict.status == REFUTED
    shift = verdict.counterexample["y"] & 0xFFFFFFFF
    assert shift >= 32, (
        "the counterexample should be an out-of-range shift, which is the only "
        f"way these two sides differ; got y={shift}")


def test_vacuous_precondition_is_not_a_proof():
    rule = parse_rules("""
        rule impossible {
          origin test
          expect proven
          pre    { C > 0 && C < 0 }
          match  { %r = mul %x, C }
          rewrite{ %r = add %x, 999 }
        }
    """)[0]
    assert verify_rule(rule).status == VACUOUS, (
        "a rule whose precondition no input satisfies proves trivially and "
        "must not be counted as a proof")


def test_stealth_rules_evade_uniform_testing_but_not_the_solver():
    """The measurement this project is built around: testing is budget-bound,
    proving is not."""
    evaded = 0
    for rule in STEALTH:
        assert verify_rule(rule).status == REFUTED
        uniform = run_difftest(rule, trials=10_000, seed=1, boundary_rate=0.0)
        if not uniform.found:
            evaded += 1
    assert evaded == len(STEALTH), (
        "every stealth rule is supposed to survive uniform random testing at "
        "10,000 trials; if one no longer does, it is not testing what it claims")


def test_solver_settles_every_library_rule_within_budget():
    slow = [r.name for r in ALL_RULES
            if verify_rule(r, timeout_ms=10_000).status == INCONCLUSIVE]
    assert not slow, f"solver timed out on {slow}"


@pytest.mark.parametrize("rule", HARD, ids=lambda r: r.name)
def test_unsettled_rules_are_never_proven(rule):
    """The safety half of the timeout story.

    These queries are at the solver's capability boundary and their verdict is
    genuinely unstable across runs, so asserting REFUTED would be asserting a
    flake. What must hold is that an unsettled rule is never reported as
    PROVEN: a timeout costs coverage, never soundness.
    """
    status = verify_rule(rule, timeout_ms=5_000).status
    assert status != PROVEN, (
        f"{rule.name} was reported PROVEN; a query the solver cannot settle "
        f"must never be counted as a proof")
    assert status in {REFUTED, INCONCLUSIVE}


def test_profitability_is_independent_of_soundness():
    """add_split_or_and is proven and must still be held back."""
    rule = next(r for r in TEXTBOOK if r.name == "add_split_or_and")
    assert verify_rule(rule).status == PROVEN
    assert not is_profitable(rule), (
        "a rule that turns one instruction into three is sound but is not an "
        "optimisation; the applier must not fire it")


# ------------------------------------------------------------ DSL errors ----

def test_rewrite_cannot_invent_a_register():
    with pytest.raises(RuleTypeError):
        parse_rules("""
            rule bad { origin test expect proven
              match  { %r = add %x, 1 }
              rewrite{ %r = add %nowhere, 1 }
            }
        """)


def test_sides_must_share_a_root():
    with pytest.raises(RuleSyntaxError):
        parse_rules("""
            rule bad { origin test expect proven
              match  { %r = add %x, 1 }
              rewrite{ %q = add %x, 1 }
            }
        """)


def test_type_confusion_is_rejected():
    with pytest.raises(RuleTypeError):
        parse_rules("""
            rule bad { origin test expect proven
              match  { %c = icmp eq %x, 0
                       %r = add %c, 1 }
              rewrite{ %r = add %x, 1 }
            }
        """)


def test_unknown_opcode_is_rejected():
    with pytest.raises(RuleSyntaxError):
        parse_rules("""
            rule bad { origin test expect proven
              match  { %r = frobnicate %x, 1 }
              rewrite{ %r = add %x, 1 }
            }
        """)
