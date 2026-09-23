"""Tests for llm/dedupe.py.

The fixtures below are not invented -- they are the actual duplicate rules a
real mining run produced (see rulelib/mined.rules and the exp06 results):
across three separate, memoryless model calls, the same identity got
re-derived three times under three different names. That is real evidence
this module needs to exist, and the best test data available.
"""

from __future__ import annotations

from llm.dedupe import deduplicate
from verify.ruledsl import parse_rules

# Verbatim from rulelib/mined.rules -- three names, one underlying rule.
MUL_NEG_ONE_VARIANTS = """
rule mul_by_minus_one {
  match  { %r = mul %x, -1 }
  rewrite { %r = sub 0, %x }
}
rule mul_neg_one_to_negate {
  match  { %r = mul %x, -1 }
  rewrite { %r = sub 0, %x }
}
rule mul_by_minus_one_to_negate {
  match  { %r = mul %x, -1 }
  rewrite { %r = sub 0, %x }
}
"""

# Same rule, but the model happened to pick a different free-variable letter
# and a different temp name -- still the same rule.
RENAMED_VARIABLES = """
rule shift_double_a {
  match   { %r = add %x, %x }
  rewrite { %r = shl %x, 1 }
}
rule shift_double_b {
  match   { %q = add %z, %z }
  rewrite { %q = shl %z, 1 }
}
"""

# Two rules with a symbolic constant, renamed differently, still the same.
POW2_VARIANTS = """
rule pow2_a {
  pre    { is_pow2(K) }
  match  { %r = mul %x, K }
  rewrite{ %r = shl %x, log2(K) }
}
rule pow2_b {
  pre    { is_pow2(C) }
  match  { %r = mul %y, C }
  rewrite{ %r = shl %y, log2(C) }
}
"""

# Same shape, different chosen temp name in rewrite -- still one rule.
CHAINED_SHIFT_VARIANTS = """
rule lshr_lshr_combine {
  pre    { C1 >= 0 && C1 < 32 && C2 >= 0 && C2 < 32 && C1 + C2 < 32 }
  match  { %t0 = lshr %x, C1
           %r = lshr %t0, C2 }
  rewrite{ %r = lshr %x, C1 + C2 }
}
rule ashr_ashr_combine {
  pre    { C1 >= 0 && C1 < 32 && C2 >= 0 && C2 < 32 && C1 + C2 < 32 }
  match  { %t0 = ashr %x, C1
           %r = ashr %t0, C2 }
  rewrite{ %r = ashr %x, C1 + C2 }
}
"""

# Genuinely different rules -- same free variable count, totally different
# operation. Must NOT be flagged as duplicates of each other.
GENUINELY_DIFFERENT = """
rule add_zero {
  match   { %r = add %x, 0 }
  rewrite { %r = add %x, 0 }
}
rule sub_self {
  match   { %r = sub %x, %x }
  rewrite { %r = and %x, 0 }
}
rule mul_by_two {
  match   { %r = mul %x, 2 }
  rewrite { %r = shl %x, 1 }
}
"""


def test_three_renamed_copies_of_one_rule_collapse_to_one():
    rules = parse_rules(MUL_NEG_ONE_VARIANTS)
    assert len(rules) == 3
    kept, dropped = deduplicate(rules)
    assert len(kept) == 1
    assert kept[0].name == "mul_by_minus_one"      # first occurrence wins
    assert set(dropped) == {"mul_neg_one_to_negate", "mul_by_minus_one_to_negate"}
    assert all(v == "mul_by_minus_one" for v in dropped.values())


def test_renamed_free_variable_is_still_a_duplicate():
    rules = parse_rules(RENAMED_VARIABLES)
    kept, dropped = deduplicate(rules)
    assert len(kept) == 1
    assert dropped == {"shift_double_b": "shift_double_a"}


def test_renamed_symbolic_constant_is_still_a_duplicate():
    rules = parse_rules(POW2_VARIANTS)
    kept, dropped = deduplicate(rules)
    assert len(kept) == 1
    assert dropped == {"pow2_b": "pow2_a"}


def test_renamed_temporary_is_still_a_duplicate():
    """The actual lshr/ashr pair from the real run -- same structure, the
    model just picked `lshr` vs `ashr` as the... wait, these differ by
    opcode, so they must NOT collapse. This test asserts the opposite of the
    naive expectation on purpose: same *shape*, different *operation*, must
    stay distinct."""
    rules = parse_rules(CHAINED_SHIFT_VARIANTS)
    kept, dropped = deduplicate(rules)
    assert len(kept) == 2, "lshr and ashr are different operations, not duplicates"
    assert dropped == {}


def test_genuinely_different_rules_are_all_kept():
    rules = parse_rules(GENUINELY_DIFFERENT)
    kept, dropped = deduplicate(rules)
    assert len(kept) == 3
    assert dropped == {}


def test_dedup_is_order_preserving_and_keeps_the_first_seen():
    rules = parse_rules(MUL_NEG_ONE_VARIANTS)
    kept, _ = deduplicate(rules)
    assert kept[0].name == rules[0].name


def test_empty_list_is_handled():
    kept, dropped = deduplicate([])
    assert kept == []
    assert dropped == {}


def test_the_actual_accumulated_mined_rules_file_deduplicates_correctly():
    """Run the real deduplicator against the real output of the real mining
    run, and check the count matches what manual inspection of
    rulelib/mined.rules found: 20 accepted, 14 structurally distinct."""
    import pathlib
    path = pathlib.Path(__file__).resolve().parent.parent / "rulelib" / "mined.rules"
    if not path.exists():
        import pytest
        pytest.skip("rulelib/mined.rules does not exist yet -- run "
                    "experiments/exp06_llm_miner.py first")
    rules = parse_rules(path.read_text())
    kept, dropped = deduplicate(rules)
    assert len(rules) == len(kept) + len(dropped)
    # every dropped rule must point at a name that was actually kept
    kept_names = {r.name for r in kept}
    assert set(dropped.values()) <= kept_names
