# MiniC — static and dynamic semantics (v0.1, frozen)

This document is normative. `verify/smt_encode.py` (symbolic, Z3) and
`verify/interp.py` (concrete, Python) are two independent implementations of
it, and `experiments/exp04_difftest_vs_smt.py` cross-checks them against each
other on every refutation. If they disagree, one of them is wrong and the
verifier cannot be trusted; that check is part of the test suite.

## 1. Why MiniC exists

Alive2 needs `poison` and `undef` because LLVM IR inherits C's undefined
behaviour. That machinery is the main reason bounded translation validation
returns *inconclusive* so often in practice (Kwon et al. report ~20% of TSVC
loops unverifiable even after LLM-Vectorizer's verification-friendly
rewrites). MiniC removes the cause rather than modelling it: **every operation
is total, and every operation that C leaves undefined either wraps or traps.**

The cost is honesty about scope — MiniC is not C, and a result on MiniC is not
a result on C. See §7.

## 2. Types

| Type     | Domain                                  | SMT sort   |
|----------|-----------------------------------------|------------|
| `int`    | 32-bit two's complement, −2³¹ … 2³¹−1   | `(_ BitVec 32)` |
| `bool`   | `{true, false}`                          | `(_ BitVec 1)`  |
| `int[N]` | N-element array of `int`, N a positive literal | array of BV32 |
| `void`   | no value; legal only as a return type    | —          |

There is no implicit conversion between `int` and `bool`. `if (x)` where `x`
is `int` is a type error (`E0203`), not a zero-test.

Arrays are fixed-size, declared with a literal bound, not first-class: they may
not be assigned, returned, or compared. Array parameters are passed by
reference.

## 3. Observable behaviour and traps

A MiniC execution has exactly one of two outcomes:

- it **returns** a value, or
- it **traps**.

A trap is observable. It is *not* undefined behaviour: it is a specific,
defined outcome that a correct transformation must preserve. This single
decision is what makes peephole equivalence decidable in QF_BV.

Trapping operations:

| Construct        | Traps when                            |
|------------------|---------------------------------------|
| `a / b`, `a % b` | `b == 0`                              |
| `a << b`, `a >> b` | `b < 0` or `b >= 32`                |
| `a[i]`           | `i < 0` or `i >= N`                   |

Nothing else traps. In particular arithmetic overflow does **not** trap.

## 4. Integer operations

All `int` arithmetic is modulo 2³² (two's complement wraparound). Overflow is
defined, not undefined.

| Op     | Meaning                                                                 |
|--------|-------------------------------------------------------------------------|
| `+ - *`| wrapping `bvadd`, `bvsub`, `bvmul`                                       |
| `/`    | `bvsdiv`: truncation **toward zero**. `INT_MIN / -1 == INT_MIN` (wraps). |
| `%`    | `bvsrem`: sign follows the **dividend**; `a % b == a - (a / b) * b`. `INT_MIN % -1 == 0`. |
| `<<`   | `bvshl`                                                                  |
| `>>`   | **arithmetic** right shift `bvashr` (operands are signed)                |
| `& \| ^ ~` | bitwise                                                              |
| unary `-` | `bvneg`; `-INT_MIN == INT_MIN`                                        |
| `< <= > >=` | **signed** comparison → `bool`                                      |
| `== !=` | equality on `int` or on `bool` → `bool`                                 |

There is no unsigned type in MiniC v0.1. `lshr` exists in the IR (it is needed
to express useful rewrites) but has no surface syntax.

### Trap propagation

Evaluation is strict and left-to-right. An expression traps if any
subexpression traps, or if the operation itself traps. Formally each
expression denotes a pair `(v, t)` where `t` is a boolean trap flag:

```
⟦a op b⟧ = (v_a op v_b,  t_a ∨ t_b ∨ traps(op, v_a, v_b))
```

The value component of a trapped expression is unconstrained; nothing may
observe it.

### Short-circuit operators

`&&` and `||` are the **only** non-strict operators:

```
⟦a && b⟧ = (v_a ∧ v_b,  t_a ∨ (v_a ∧ t_b))
⟦a || b⟧ = (v_a ∨ v_b,  t_a ∨ (¬v_a ∧ t_b))
```

So `i < n && a[i] > 0` does not trap when `i >= n`. This matters: a
transformation that replaces `&&` with `&` is *not* semantics-preserving even
though it preserves the value, because it can introduce a trap.

## 5. Equivalence

Two fragments `P` and `Q` over the same free variables are **equivalent**
(written `P ≡ Q`) iff for every assignment `σ` to those variables:

```
P ≡ Q  ⟺  ∀σ.  t_P(σ) = t_Q(σ)  ∧  (¬t_P(σ) → v_P(σ) = v_Q(σ))
```

This is *trap-preserving* equivalence. It is strictly stronger than value
equivalence on non-trapping inputs, and it is what `verify/smt_encode.py`
discharges. The solver is given the negation

```
∃σ.  t_P ≠ t_Q  ∨  (¬t_P ∧ ¬t_Q ∧ v_P ≠ v_Q)
```

and a rule is `PROVEN` only when that query is `unsat`. `sat` yields a
concrete counterexample; `unknown` (timeout) is recorded as `INCONCLUSIVE` and
is never treated as a proof.

Because a rule's operands and constants are **symbolic**, one `unsat` covers
every instance of the pattern, not the single instance a proposer happened to
look at. This is the property that lets the model run offline: the compile-time
artifact is a finite set of proven rules, and applying them needs no inference.

## 6. Statements

| Form | Meaning |
|------|---------|
| `T x = e;` | declares `x` in the enclosing block; shadowing an outer `x` is legal, redeclaring in the same block is `E0102` |
| `x = e;` | assignment; `x` must be an lvalue (`ident` or `ident[e]`) |
| `if (c) {…} else {…}` | `c` must be `bool`; bodies are blocks (see grammar) |
| `while (c) {…}` | `c` must be `bool` |
| `for (init; c; step) {…}` | `init` may declare; its scope is the loop |
| `return e;` | type must match the enclosing function's return type |

Every non-`void` function must return on every path (`E0401`). There is no
fall-off-the-end.

Uninitialised reads are prevented statically: a local with no initialiser is
zero-initialised (`int` → `0`, `bool` → `false`, arrays → all zero). This is a
deliberate choice over C's undefined read, and again it exists to keep the SMT
encoding total.

## 7. What MiniC deliberately excludes (v0.1)

Pointers, the heap, structs, floats, `unsigned`, `goto`, `switch`, `break`,
`continue`, variadic and recursive-type declarations, and separate compilation.

Floats are excluded specifically because floating-point equivalence is not the
same problem: reassociation is unsound, and the SMT theory (FPA) is far more
expensive than QF_BV. Pointers are excluded because aliasing is precisely what
makes Alive2 imprecise, and modelling it properly is a project of its own.

**A result measured on MiniC is a result about a pipeline, not about C.** Every
number this project reports must be qualified that way.
