# 5. The rule DSL

**What this is for:** the small language rewrite rules are written in — both the 37 hand-written rules in `rulelib/` and everything Module B's proposer generates. This is what a "proposal" actually *is* in this project: not a rewritten program, but a rule written in this grammar.

**Key file:** `verify/ruledsl.py`

## What a rule looks like

```
rule mul_pow2_to_shl {
  pre     { is_pow2(C) }
  match   { %r = mul %x, C }
  rewrite { %r = shl %x, log2(C) }
}
```

- `%x` is a **free register** — a stand-in for *any* 32-bit value.
- `C` (capitalized, no `%`) is a **symbolic constant** — also a stand-in for any value, but one you can constrain with a precondition.
- `match` is the pattern to look for; `rewrite` is what to replace it with.

The critical rule, enforced by the parser: **the last instruction of `match` and the last instruction of `rewrite` must define the same register name.** That shared register is the one whose value actually gets compared when checking whether the rule is correct — see [06-smt-verification.md](06-smt-verification.md).

## How parsing works

`_tokenize()` is a small regex-based tokenizer (see the `_TOKEN_RE` pattern) — simpler than the main compiler's lexer because this language is much smaller. `_Parser` is, like the main parser, hand-written recursive descent: `parse_rule()` reads the `origin`/`expect`/`note`/`pre`/`match`/`rewrite` sections in any order, `parse_instr_block()` reads a `{ ... }` list of instructions, and `parse_const_expr()` handles arithmetic inside preconditions with normal C-style operator precedence (`|` binds loosest, `*`/`/`/`%` bind tightest — see the `_LEVELS` list).

## Type inference: catching nonsense before it reaches the solver

`_infer_types()` runs right after parsing and does two things:

1. **Assigns a type to every register** — `i32` for ordinary values, `i1` for anything that's the result of `icmp`, `select`, or `not`. If a rule uses the same register name as both (for example, calling the boolean-only `not` on what's clearly a 32-bit value elsewhere in the rule), this raises `RuleTypeError` immediately, with a message naming exactly which instruction caused the conflict.
2. **Figures out which registers are "free variables"** — used in `match` but never defined there — versus which are just internal temporaries, and checks that `rewrite` never reads a register that neither `match` defined nor `rewrite` itself defined earlier. A rewrite can't invent a value out of nowhere.

This matters in practice, not just in theory: when Module B's proposer was pointed at this grammar for the first time, its very first batch of proposals hit exactly this type-checking logic — see [08-module-b-llm.md](08-module-b-llm.md) for what that looked like and how it was fixed.

## The opcode set

Deliberately narrow: the eleven binary IR operations (`add sub mul sdiv srem and or xor shl ashr lshr`), plus `icmp <pred>` (comparison, ten predicates), `select` (three-way choice), and `not` (boolean negation only — **not** bitwise complement, which is expressed as `xor %x, -1` instead). See `VALID_OPS` for the exhaustive list.

## See it yourself

```bash
./provitc.py verify rulelib/textbook.rules      # parse + verify every rule in one file
cat rulelib/textbook.rules                       # read real, working rules
```

`tests/test_verify.py` has dedicated tests for the type-checking failure modes described above (`test_rewrite_cannot_invent_a_register`, `test_sides_must_share_a_root`, `test_type_confusion_is_rejected`).
