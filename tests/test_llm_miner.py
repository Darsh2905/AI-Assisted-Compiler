"""Tests for the LLM rule miner (Module B).

None of these make a network call. `mine()` takes a plain callable, so every
test scripts a fake "model" that returns fixed text -- the same text shapes a
real model is likely to produce: a correct rule, a malformed one, an
obviously-wrong one, a subtly-wrong one, and prose the model was told not to
add. This lets the whole trust boundary (extract -> parse -> differential
test -> SMT) be exercised deterministically, before a single API call exists.

Each fixture rule below is a *fresh* one, distinct from anything in
`rulelib/`, so these tests exercise the same machinery on genuinely different
input rather than re-running the existing library's fixtures under a new
name.
"""

from __future__ import annotations

import pytest

from llm.miner import Proposal, _dedupe_name, extract_rule_blocks, mine
from verify.ruledsl import parse_rules
from verify.smt_encode import PROVEN, REFUTED, verify_rule

# ---------------------------------------------------------------- fixtures --

CORRECT_PROFITABLE = """\
rule mul_four_to_shl2 {
  match   { %r = mul %x, 4 }
  rewrite { %r = shl %x, 2 }
}"""

CORRECT_NOT_PROFITABLE = """\
rule sub_zero_is_add_zero {
  match   { %r = sub %x, 0 }
  rewrite { %r = add %x, 0 }
}"""

MALFORMED_BAD_OPCODE = """\
rule uses_a_fake_opcode {
  match   { %r = frobnicate %x, 1 }
  rewrite { %r = add %x, 1 }
}"""

MALFORMED_ROOT_MISMATCH = """\
rule roots_dont_match {
  match   { %r = add %x, 1 }
  rewrite { %q = add %x, 1 }
}"""

# Classic sign-of-truncation bug: sdiv truncates toward zero, ashr toward
# -inf, so they disagree on every negative operand -- an extremely common
# value that both uniform and boundary-seeded sampling find almost
# immediately.
OBVIOUSLY_WRONG = """\
rule my_sdiv_two_to_ashr {
  match   { %r = sdiv %x, 2 }
  rewrite { %r = ashr %x, 1 }
}"""

# Same shape as rulelib/stealth.rules' magic-constant canary: an identity for
# every constant except one arbitrary, non-"interesting" value that neither
# uniform nor boundary-seeded sampling has any reason to draw.
SUBTLE_WRONG_SURVIVES_DIFFTEST = """\
rule mul_identity_except_one_magic_value {
  match  { %r = mul %x, C }
  rewrite{
    %t0 = icmp eq C, 1515870810
    %t1 = mul %x, C
    %r  = select %t0, 0, %t1
  }
}"""


# ------------------------------------------------------- extract_rule_blocks --

def test_extracts_a_single_block():
    blocks = extract_rule_blocks(CORRECT_PROFITABLE)
    assert len(blocks) == 1
    assert blocks[0].strip().startswith("rule mul_four_to_shl2")


def test_extracts_multiple_back_to_back_blocks():
    text = CORRECT_PROFITABLE + "\n\n" + MALFORMED_BAD_OPCODE
    blocks = extract_rule_blocks(text)
    assert len(blocks) == 2
    assert "mul_four_to_shl2" in blocks[0]
    assert "uses_a_fake_opcode" in blocks[1]


def test_ignores_prose_between_blocks():
    text = ("Sure, here are some rules:\n\n" + CORRECT_PROFITABLE +
           "\n\nAnd here's another one:\n\n" + MALFORMED_BAD_OPCODE +
           "\n\nHope that helps!")
    blocks = extract_rule_blocks(text)
    assert len(blocks) == 2, "prose outside the braces must not break extraction"


def test_ignores_a_truncated_unbalanced_block():
    """A response cut off mid-rule (e.g. hit max_tokens) must not crash
    extraction or be mistaken for a complete, if malformed, rule."""
    text = CORRECT_PROFITABLE + "\n\nrule truncated_by_token_limit {\n  match { %r = add %x, 1"
    blocks = extract_rule_blocks(text)
    assert len(blocks) == 1
    assert "mul_four_to_shl2" in blocks[0]


def test_empty_response_yields_no_blocks():
    assert extract_rule_blocks("I don't have any rules to propose right now.") == []


def test_nested_braces_inside_a_block_do_not_split_it():
    # select/predicate expressions can themselves contain parens, which must
    # not be confused with the brace-balancing that delimits the rule.
    blocks = extract_rule_blocks(SUBTLE_WRONG_SURVIVES_DIFFTEST)
    assert len(blocks) == 1
    assert blocks[0].count("{") == blocks[0].count("}")


# ------------------------------------------------------------- _dedupe_name --

def test_dedupe_leaves_a_unique_name_alone():
    block, name = _dedupe_name(CORRECT_PROFITABLE, taken=set())
    assert name == "mul_four_to_shl2"
    assert block == CORRECT_PROFITABLE


def test_dedupe_renames_on_collision():
    block, name = _dedupe_name(CORRECT_PROFITABLE, taken={"mul_four_to_shl2"})
    assert name == "mul_four_to_shl2_2"
    assert "mul_four_to_shl2_2" in block
    # parse_rules should now see the renamed block as valid, distinct DSL text
    rules = parse_rules(block)
    assert rules[0].name == "mul_four_to_shl2_2"


def test_dedupe_increments_past_multiple_collisions():
    taken = {"mul_four_to_shl2", "mul_four_to_shl2_2"}
    _, name = _dedupe_name(CORRECT_PROFITABLE, taken=taken)
    assert name == "mul_four_to_shl2_3"


# --------------------------------------------------------------------- mine --

def fake_proposer(*texts: str):
    """Returns a propose_fn that ignores `n` and returns fixed text -- the
    scripted stand-in for a model response."""
    joined = "\n\n".join(texts)
    return lambda n: joined


def test_mine_accepts_a_correct_profitable_rule():
    run = mine(fake_proposer(CORRECT_PROFITABLE), n=1)
    assert run.total == 1
    p = run.proposals[0]
    assert p.parse_ok
    assert p.difftest_verdict == "SURVIVED"
    assert p.smt_status == PROVEN
    assert p.profitable is True
    assert p.accepted is True
    assert p.match_cost is not None and p.rewrite_cost is not None
    assert p.rewrite_cost < p.match_cost


def test_mine_marks_proven_but_unprofitable_rule_not_accepted():
    """Correctness and profitability are independent checks -- this is the
    same finding the hand-written library ran into with add_split_or_and,
    now asserted for whatever a model might propose."""
    run = mine(fake_proposer(CORRECT_NOT_PROFITABLE), n=1)
    p = run.proposals[0]
    assert p.parse_ok
    assert p.smt_status == PROVEN
    assert p.profitable is False, "sub->add is correct but not cost-reducing"
    assert p.accepted is False


def test_mine_rejects_malformed_opcode():
    run = mine(fake_proposer(MALFORMED_BAD_OPCODE), n=1)
    p = run.proposals[0]
    assert not p.parse_ok
    assert p.parse_error is not None
    assert p.difftest_verdict is None, "a rule that never parsed must never reach testing"
    assert p.smt_status is None
    assert not p.accepted


def test_mine_rejects_mismatched_roots():
    run = mine(fake_proposer(MALFORMED_ROOT_MISMATCH), n=1)
    p = run.proposals[0]
    assert not p.parse_ok
    assert "root" in p.parse_error or "same" in p.parse_error.lower() or \
           p.parse_error is not None


def test_mine_catches_an_obvious_bug_with_the_cheap_prefilter():
    """The sdiv/ashr sign bug is common enough that differential testing
    should catch it without ever calling the solver -- this is exactly the
    cost-saving the prefilter exists for."""
    run = mine(fake_proposer(OBVIOUSLY_WRONG), n=1)
    p = run.proposals[0]
    assert p.parse_ok
    assert p.difftest_verdict == "REFUTED"
    assert p.smt_status is None, (
        "a rule refuted by the cheap prefilter should never reach the solver")
    assert not p.accepted


def test_mine_headline_case_subtle_bug_survives_difftest_but_solver_catches_it():
    """This is the actual measurement this project exists to make: a
    proposal that passes the cheap check and is still wrong."""
    run = mine(fake_proposer(SUBTLE_WRONG_SURVIVES_DIFFTEST), n=1,
              difftest_trials=20_000)
    p = run.proposals[0]
    assert p.parse_ok
    assert p.difftest_verdict == "SURVIVED", (
        "if this starts failing, the fixture no longer demonstrates the gap "
        "the miner is built to measure")
    assert p.smt_status == REFUTED
    assert p.smt_counterexample, "the solver must hand back a concrete witness"
    assert not p.accepted


def test_mine_flags_prose_outside_rule_blocks():
    text = "Here are your rules:\n\n" + CORRECT_PROFITABLE
    run = mine(lambda n: text, n=1)
    assert run.extra_text_present is True


def test_mine_does_not_flag_clean_output():
    run = mine(fake_proposer(CORRECT_PROFITABLE), n=1)
    assert run.extra_text_present is False


def test_mine_renames_on_collision_with_the_existing_library():
    run = mine(fake_proposer(CORRECT_PROFITABLE), n=1,
              existing_rule_names={"mul_four_to_shl2"})
    assert run.proposals[0].rule_name == "mul_four_to_shl2_2"
    assert run.proposals[0].parse_ok


def test_mine_handles_zero_proposals_gracefully():
    run = mine(lambda n: "", n=5)
    assert run.total == 0
    assert run.summary()["requested"] == 5
    assert run.summary()["extracted"] == 0


def test_mine_processes_a_mixed_batch_independently():
    """One malformed rule in a batch must not affect the others -- each
    proposal is isolated before it is parsed."""
    run = mine(fake_proposer(CORRECT_PROFITABLE, MALFORMED_BAD_OPCODE,
                             OBVIOUSLY_WRONG, SUBTLE_WRONG_SURVIVES_DIFFTEST),
              n=4, difftest_trials=20_000)
    assert run.total == 4
    by_name = {p.rule_name: p for p in run.proposals}
    assert by_name["mul_four_to_shl2"].accepted
    assert not by_name["uses_a_fake_opcode"].parse_ok
    assert by_name["my_sdiv_two_to_ashr"].difftest_verdict == "REFUTED"
    assert by_name["mul_identity_except_one_magic_value"].smt_status == REFUTED


# ------------------------------------------------------------------ summary --

def test_summary_counts_match_a_manual_tally():
    run = mine(fake_proposer(CORRECT_PROFITABLE, CORRECT_NOT_PROFITABLE,
                             MALFORMED_BAD_OPCODE, OBVIOUSLY_WRONG,
                             SUBTLE_WRONG_SURVIVES_DIFFTEST),
              n=5, difftest_trials=20_000)
    s = run.summary()
    assert s["requested"] == 5
    assert s["extracted"] == 5
    assert s["malformed"] == 1
    assert s["parsed"] == 4
    assert s["difftest_refuted"] == 1          # the sdiv/ashr one
    assert s["survived_difftest"] == 3         # both correct + the canary
    assert s["smt_refuted_after_surviving_difftest"] == 1   # the canary
    assert s["proven"] == 2                    # both correct ones
    assert s["accepted"] == 1                  # only the profitable correct one
    # internal consistency: every extracted block is accounted for exactly once
    assert s["malformed"] + s["parsed"] == s["extracted"]
    assert s["difftest_refuted"] + s["survived_difftest"] == s["parsed"]


# ---------------------------------------------------- accepted rules persist --

def test_an_accepted_rule_round_trips_through_render_and_still_verifies():
    """This is the property that makes it safe to write accepted proposals to
    rulelib/mined.rules: render() -> save -> reparse -> re-verify must agree
    with the in-memory verdict, or a saved rule could silently differ from
    what was actually proven."""
    run = mine(fake_proposer(CORRECT_PROFITABLE), n=1)
    p = run.proposals[0]
    assert p.accepted

    rendered = p.rule.render()
    reparsed = parse_rules(rendered)
    assert len(reparsed) == 1
    v = verify_rule(reparsed[0])
    assert v.status == PROVEN


def test_proposal_to_dict_is_json_serialisable():
    import json
    run = mine(fake_proposer(CORRECT_PROFITABLE, MALFORMED_BAD_OPCODE), n=2)
    for p in run.proposals:
        json.dumps(p.to_dict())          # raises if anything isn't serialisable
