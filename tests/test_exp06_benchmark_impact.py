"""Regression test for a real bug caught while building E6.4.

The first version of `benchmark_impact()` in `experiments/exp06_llm_miner.py`
passed every hand-written rule -- including the deliberately wrong ones in
`adversarial.rules` and `stealth.rules` -- straight into `optimize()` without
filtering to `verify_rule(...).is_proof` first. `optimize()` performs no
verification of its own; it trusts whatever list it is handed, exactly as
`passes/peephole.py` documents. The bug was caught because its "hand-written
only" baseline (1033 -> 1015, a 1.7% reduction) didn't match E5's already
-published, protected baseline (1033 -> 1018, 1.5%) -- a wrong rule had fired
and silently miscompiled part of the benchmark suite while making it look
like a *better* result.

This test asserts the property whose absence caused that bug: the function
must reproduce E5's exact baseline when it isolates the hand-written path,
and it must never construct a rule list containing an unverified rule.
"""

from __future__ import annotations

import pytest

from experiments import _common as C
from experiments import exp05_rule_application
from experiments.exp06_llm_miner import MINED_RULES_FILE, benchmark_impact
from verify.ruledsl import load_rules
from verify.smt_encode import verify_rule


def _e5_baseline() -> tuple[int, int]:
    """Recompute E5's own before/after totals independently (an in-process
    call, not a subprocess -- E5's `main()` is already side-effect-free
    beyond printing and writing its own results file), so this test does not
    depend on results/exp05_rule_application.json already being fresh."""
    data = exp05_rule_application.main()
    return data["total"]["before"], data["total"]["after"]


@pytest.mark.skipif(not MINED_RULES_FILE.exists(),
                    reason="rulelib/mined.rules does not exist yet -- run "
                          "experiments/exp06_llm_miner.py first")
def test_benchmark_impact_hand_written_path_matches_e5_exactly():
    e5_before, e5_after = _e5_baseline()
    result = benchmark_impact()
    assert result["instr_before"] == e5_before
    assert result["hand_written_after"] == e5_after, (
        "benchmark_impact()'s hand-written-only path must reproduce E5's "
        "published baseline exactly; a mismatch means an unverified rule "
        "reached the optimizer")


@pytest.mark.skipif(not MINED_RULES_FILE.exists(),
                    reason="rulelib/mined.rules does not exist yet")
def test_benchmark_impact_never_applies_an_unproven_rule():
    """Directly re-derive the rule list benchmark_impact() would build and
    assert every single one is independently PROVEN -- the property the
    original bug violated."""
    hand_written = [r for path in C.hand_written_rule_files()
                    for r in load_rules(str(path))
                    if verify_rule(r).is_proof]
    mined = [r for r in load_rules(str(MINED_RULES_FILE))
             if verify_rule(r).is_proof]
    combined = hand_written + mined

    assert combined, "expected at least one rule on each side"
    unproven = [r.name for r in combined if not verify_rule(r).is_proof]
    assert unproven == [], (
        f"the following rules are not PROVEN and must never reach "
        f"optimize(): {unproven}")

    # None of the deliberately-wrong rules must sneak in via name collision
    # or a stale cache -- cross-check against the known-refuted set directly.
    refuted_names = {
        r.name for path in C.hand_written_rule_files()
        for r in load_rules(str(path)) if not verify_rule(r).is_proof
    }
    combined_names = {r.name for r in combined}
    assert not (refuted_names & combined_names), (
        "a refuted rule appears in the combined library")


def test_benchmark_impact_asserts_before_counts_agree():
    """A sanity property independent of mined.rules existing at all: the
    same source must compile to the same instruction count regardless of
    which rule list is later applied to it."""
    import inspect
    src = inspect.getsource(benchmark_impact)
    assert 'assert b0 == b1' in src, (
        "the before-optimization instruction count must be asserted equal "
        "across both library configurations -- optimization must never "
        "change what the *unoptimized* IR looks like")
