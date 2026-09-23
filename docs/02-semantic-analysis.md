# 2. Semantic analysis and diagnostics

**What this is for:** walking the parsed tree and checking it actually makes sense — every variable declared before use, every operator given the right types, every function returning on every path. This is also where every error message gets built.

**Key files:** `frontend/sema.py`, `frontend/diagnostics.py`

## The `Analyzer` class: one pass, two jobs

`frontend/sema.py`'s `Analyzer` does two things as it walks the tree:

1. **Assigns a type to every expression** (fills in the `.ty` field left blank by the parser).
2. **Checks that assignment is legal** at every point (a `bool` can't initialize an `int`, a condition must be `bool`, and so on) and **records a diagnostic** for every violation instead of stopping at the first one.

### Scopes

`Scope` is a simple linked structure — each scope has a dictionary of names and a pointer to its parent scope. `push()`/`pop()` create and destroy a scope every time the analyzer enters or leaves a block (`{ }`), a function, or a loop body. Looking up a variable (`Scope.lookup()`) walks outward through parent scopes until it finds the name or runs out of scopes — this is what makes shadowing work correctly (an inner `x` hides an outer `x`, but only inside its own block).

### Checking one expression

`check_expr()` is a big dispatch: it looks at what kind of node it's holding (`IntLit`, `VarRef`, `Binary`, ...) and calls the matching private method. The interesting one is `_check_binary()`, which groups operators by what they expect:

- `ARITH_OPS` (`+ - * / % & | ^ << >>`) need two `int`s and produce `int`.
- `COMPARE_OPS` (`< <= > >=`) need two `int`s and produce `bool`.
- `EQUALITY_OPS` (`== !=`) need both sides to be the *same* type and produce `bool`.
- `LOGICAL_OPS` (`&& ||`) need two `bool`s and produce `bool`.

If a rule is violated, it calls `self.error(...)` — which doesn't raise an exception. It just appends a diagnostic to a list and lets the walk continue. That's the whole trick behind reporting multiple independent errors from one compile: **nothing stops early.**

### Return-path checking

`always_returns()` is a small recursive function, separate from the main walk, that answers "does every path through this statement definitely hit a `return`?" A `Block` always-returns if *any* one of its statements does (once you hit a `return`, the rest is unreachable). An `If` always-returns only if *both* its `then` and `else` branches do — and critically, **a loop body is never assumed to run**, so a `while`/`for` never counts as guaranteeing a return, even if the loop body has one. This is intentionally the conservative, standard choice real compilers make.

## Diagnostics: not just a string

`frontend/diagnostics.py`'s `Diagnostic` is a small data class holding a lot more than a message:

```python
Diagnostic(code, span, message, expected=None, found=None, in_scope=[], notes=[])
```

- `render()` produces the human-readable, caret-pointing text you see in the terminal.
- `to_context()` produces a **structured dictionary** instead — the error code, the span, `expected`/`found` types, the list of every variable currently in scope (with its type), and a source-code window around the error. This is the "grounded context" the README and paper talk about: it's built from real compiler state (the actual scope table, the actual inferred types), not from re-parsing the error message as text. It exists specifically so that a future model-based repair step (Module A) would have real facts to work from instead of a vague error string — that half of Module A isn't built yet, but this substrate is, and it's fully tested.

Every error class the analyzer can produce has a stable code (`E0101` = undefined variable, `E0203` = condition must be bool, etc.), listed in `ERROR_CODES` at the top of the file.

## See it yourself

```bash
./provitc.py check demo/broken.mc      # human-readable diagnostics
./provitc.py context demo/broken.mc    # the same errors as structured JSON
```
