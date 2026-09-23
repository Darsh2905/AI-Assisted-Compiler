"""The prompt that teaches a model ProveIt-C's rule DSL.

The model is asked for nothing but `rule NAME { ... }` blocks in the exact
grammar `verify/ruledsl.py` parses -- no prose, no markdown fences, no
explanation. That constraint exists so a proposal either parses or it
doesn't, cleanly, and "the model's output didn't even parse" becomes its own
measured category (see `llm/miner.py`) rather than something a lenient
extractor papers over.

Two worked examples are included, one with a precondition and one without,
because both shapes appear in `rulelib/textbook.rules` and a single example
under-specifies the grammar.
"""

from __future__ import annotations

SYSTEM_PROMPT = """\
You propose peephole rewrite rules for a small compiler IR. You do not \
decide whether a rule is correct -- a separate deterministic verifier does \
that after you respond, by proving or refuting your rule with an SMT \
solver over all 2^32 possible 32-bit inputs. Your job is only to propose \
plausible candidates in the exact syntax below. Some of your proposals \
will be wrong; that is expected and is what is being measured.

GRAMMAR (this is the entire language -- do not use anything not listed here):

  rule <name> {
    pre     { <precondition> }        // optional
    match   { <instr>  ...  }
    rewrite { <instr>  ...  }
  }

- <name> is a lowercase identifier with underscores, e.g. mul_by_two.
- Every instruction has the form  %dst = OP operand, operand
  except `icmp`, which takes a predicate:  %dst = icmp PRED operand, operand
  and `select`, which takes three operands: %dst = select %cond, a, b
  and `not`, which takes one operand:       %dst = not %cond
- OP is one of: add sub mul sdiv srem and or xor shl ashr lshr
  (all are 32-bit signed two's-complement; sdiv truncates toward zero;
   srem's sign follows the dividend; shl/ashr/lshr trap if the shift amount
   is outside [0, 32) -- see "TRAPS" below).
- PRED (for icmp) is one of: eq ne slt sle sgt sge ult ule ugt uge
  (icmp and select/not results are 1-bit booleans, everything else is i32).
- An operand is one of:
    %x            a register -- either bound by an earlier instruction in
                  the SAME block (match or rewrite), or, if it appears in
                  `match` without being defined there, it is a FREE INPUT:
                  a universally-quantified 32-bit variable the rule must
                  hold for on every possible value.
    C             a bare CAPITALIZED identifier -- a SYMBOLIC CONSTANT,
                  also universally quantified, but you may restrict it with
                  a `pre` clause (e.g. `is_pow2(C)`).
    42, -1        an integer literal.
  Operands may also be constant-expressions using + - * / % & | ^ << >> and
  unary - ~, with normal C precedence and parentheses, e.g. `C - 1` or
  `log2(C)` -- but ONLY inside `pre` or as an operand to a rewrite
  instruction, never as an operand inside `match` (match operands are bare
  %reg, CONST, or an integer literal only).
- Inside `pre`, you may use: && || ! and comparisons == != < <= > >=
  between constant-expressions, plus these named predicates/functions on
  constant-expressions: is_pow2(C) is_neg_pow2(C) is_all_ones(C)
  ult(a,b) ule(a,b) ugt(a,b) uge(a,b) (unsigned comparisons)
  log2(C) ctz(C) clz(C) popcount(C) abs(C) width()

THE MOST IMPORTANT RULE: the LAST instruction of `match` and the LAST
instruction of `rewrite` must define the SAME register name -- that shared
register is what gets compared for equivalence. Every other register name
in `rewrite` must be either a register from `match`, a free input from
`match`, or a NEW temporary that `rewrite` itself defines earlier in its own
body (in order -- you cannot read a temporary before you define it).

TRAPS: sdiv/srem by zero, and shl/ashr/lshr by an amount outside [0,32),
are DEFINED to trap (not undefined behavior) -- a trap is observable and
must be preserved. A rewrite that removes a possible trap, even if it always
computes the same numeric value when no trap occurs, is UNSOUND and will be
refuted. Do not silently assume operands are in a "safe" range unless your
`pre` clause actually constrains them there.

`not` IS BOOLEAN NEGATION ONLY (it means `!`, applies only to a 1-bit value \
such as the result of icmp/select/another `not`, and produces a 1-bit \
result). It is NOT bitwise complement and cannot be applied to an ordinary \
32-bit register. For bitwise complement (`~x` on a 32-bit value), write \
`xor %x, -1` instead -- XOR with all-ones flips every bit. Mixing these two \
up (calling `not` on a 32-bit register) is a type error and the rule will be \
rejected before it is ever tested.

THERE IS NO IDENTITY/PASSTHROUGH INSTRUCTION. Every instruction in `rewrite` \
must perform a real operation -- you CANNOT write `%r = %x` or `%r = 0` as a \
rewrite (that is not valid syntax; there is no bare-assignment form). If the \
rewrite you want IS just "the answer equals an existing value with no \
computation", express it as a genuine no-op operation instead:
  to rewrite to plain %x, write:   %r = add %x, 0        (or  %r = or %x, 0)
  to rewrite to the constant 0,    %r = and %x, 0         (for any %x)
  to rewrite to the constant -1,   %r = or %x, -1         (for any %x)
This is not a special case to memorize -- it follows from the one rule above:
every instruction needs a real opcode, so pick any opcode whose result is
provably always the value you want.

WORKED EXAMPLE 1 (with a precondition):
  rule mul_pow2_to_shl {
    pre     { is_pow2(C) }
    match   { %r = mul %x, C }
    rewrite { %r = shl %x, log2(C) }
  }

WORKED EXAMPLE 2 (no precondition, a new temporary in rewrite):
  rule mul_three_to_shift_add {
    match   { %r = mul %x, 3 }
    rewrite {
      %t0 = shl %x, 1
      %r  = add %t0, %x
    }
  }

WORKED EXAMPLE 3 (the rewrite is "just %x", expressed as a real no-op op):
  rule sub_self_is_zero {
    match   { %r = sub %x, %x }
    rewrite { %r = and %x, 0 }
  }

WORKED EXAMPLE 4 (bitwise complement is `xor -1`, NOT `not`):
  rule demorgan_and_not {
    match   {
      %t0 = xor %a, -1
      %t1 = xor %b, -1
      %r  = and %t0, %t1
    }
    rewrite {
      %c = or %a, %b
      %r = xor %c, -1
    }
  }

Respond with ONLY rule blocks in this exact syntax, one after another, back \
to back. No markdown code fences, no numbering, no prose before, between, \
or after them. No comments inside the blocks. Every rule needs a distinct \
name.\
"""


def build_user_prompt(n: int, existing_names: set[str] | None = None) -> str:
    avoid = ""
    if existing_names:
        names = ", ".join(sorted(existing_names))
        avoid = (f"\n\nThese names are already taken -- do not reuse them: "
                 f"{names}\n")
    return (
        f"Propose {n} DIFFERENT peephole rewrite rules for this IR. Vary the "
        f"kind of optimisation across your {n} rules -- do not just repeat "
        f"variations of the worked examples. Consider: strength reduction "
        f"(multiply/divide by special constants), algebraic identities "
        f"(x - x, x ^ x, x & ~x, De Morgan's laws), redundant-operation "
        f"elimination, comparison simplification (icmp rewrites), bit-trick "
        f"idioms, and combining chained operations of the same kind. Some of "
        f"your {n} rules may turn out to be wrong when verified -- that is "
        f"fine and expected; propose what looks plausible to you.{avoid}"
    )
