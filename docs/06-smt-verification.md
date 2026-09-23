# 6. SMT verification: how a rule gets mathematically proven

**What this is for:** answering, with certainty rather than a sample, whether a rewrite rule is correct for *every* possible input — not tested, proven. This is the deterministic core the entire project is built around.

**Key files:** `verify/smt_encode.py`, `verify/interp.py`, `spec/semantics.md`

## The property being checked: trap-preserving equivalence

MiniC defines exactly three ways a program can trap: division or remainder by zero, a shift by an out-of-range amount, and an out-of-bounds array access. A trap is **observable, defined behavior** — not undefined behavior — which means a correct rewrite has to trap on exactly the same inputs the original did, not just compute the same *value* when nothing traps.

That's stronger than it sounds. `verify_rule()` in `verify/smt_encode.py` builds a single logical question and asks Z3 (an SMT solver) to answer it:

> Does there exist *any* assignment to the free registers and symbolic constants where the two sides trap differently, **or** where neither traps but they compute different values?

If Z3 proves no such assignment exists (`unsat`), the rule is `PROVEN` — for all 2³² possible values, in one query. If it finds one (`sat`), the rule is `REFUTED`, and Z3 hands back the actual numbers that break it. If the solver can't decide within the timeout, the result is `INCONCLUSIVE` and — this is important — **never treated as a proof**.

## How an instruction becomes a logical formula

The `Encoder` class walks a rule's `match` or `rewrite` instructions one at a time (`Encoder.run()`), keeping an environment that maps each register name to a `(value, trap)` pair — a Z3 bitvector expression for the value, and a Z3 boolean expression for whether evaluating it traps. `_binop()` is where the actual semantics live: `add`/`sub`/`mul`/`and`/`or`/`xor` never trap; `sdiv`/`srem` trap exactly when the divisor is zero; `shl`/`ashr`/`lshr` trap exactly when the shift amount is 32 or more (checked with `z3.UGE`, unsigned-greater-or-equal, which conveniently also catches negative shift amounts since those wrap around to huge unsigned numbers).

Traps **propagate**: if any operand's evaluation traps, the whole instruction's trap flag becomes true too (`trap = z3.Or(*[t for _, t in parts])`), so a trap anywhere in a multi-instruction rewrite correctly surfaces at the root.

## Two independent implementations, cross-checked

`verify/interp.py` is a second, completely separate implementation of the exact same semantics — a plain Python interpreter instead of a Z3 encoder, written from `spec/semantics.md` directly rather than by copying the encoder's logic. This exists because of one specific risk: if the SMT encoder had a bug that made it *agree with itself* incorrectly, nothing about the solver's `unsat`/`sat` output would reveal that.

Every counterexample the solver produces gets replayed through this second interpreter (`replay()` in `verify/difftest.py`, used throughout `tests/test_verify.py`). If the interpreter *doesn't* reproduce the disagreement Z3 claims to have found, that means the two implementations have diverged — which would be a serious bug in the verifier itself, not just in one rule. This cross-check passes 100% of the time across every rule in the project (17 out of 17 counterexamples reproduced, as reported in the paper).

## Precondition vacuity

One subtle failure mode: a rule whose precondition can *never* be true proves trivially (there's no input to find a counterexample among), which would make it look "proven" while being completely useless — it can never fire. `verify_rule()` checks this first (`VACUOUS`), before attempting the real equivalence proof, specifically so a rule like this doesn't silently inflate the count of "real" proofs.

## See it yourself

```bash
./provitc.py verify rulelib/adversarial.rules   # watch the solver refute deliberately wrong rules
```

`tests/test_verify.py::test_encoder_and_interpreter_agree_pointwise` runs the pointwise cross-check described above directly, on every rule in the project.
