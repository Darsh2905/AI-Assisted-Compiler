# 3. The intermediate representation (IR)

**What this is for:** a simpler, lower-level form of the program — one tiny operation per line — that the optimizer can pattern-match against. This is the "assembly language" of the compiler, except it's made up and designed to be easy to reason about rather than to run on real hardware.

**Key files:** `ir/ir.py`, `ir/irgen.py`, `ir/printer.py`, `ir/irparser.py`

## What an IR instruction looks like

`ir/ir.py` defines the data types. An `Instr` has an opcode (`op`, a string like `"add"` or `"mul"`), a list of operands (`args` — each one is either a `Reg`, a named temporary value like `%3`, or a `Const`, a literal number), and optionally a `result` name if it produces a value. A `Block` is just a list of instructions ending in one that jumps somewhere (`br`, `cbr`, `ret`) — a *terminator*. A `Function` is a list of blocks; a `Module` is a list of functions.

The opcode set is small on purpose (about 20 operations — `add`, `sub`, `mul`, `sdiv`, `icmp`, `shl`, `load`, `store`, `br`, `cbr`, `ret`, and a handful more). A small instruction set keeps the SMT encoding in `verify/smt_encode.py` tractable and keeps the prompt Module B's proposer has to learn short.

## Generating IR from the typed AST

`ir/irgen.py`'s `FunctionBuilder` class walks the typed AST one function at a time and emits instructions. A few decisions worth knowing:

- **Every local variable becomes a memory slot**, not a register. `int x = 5;` becomes an `alloca` (reserve a slot) followed by a `store` (write into it), and every later use of `x` becomes a `load`. This is simple and always correct, but it means the IR is **not in SSA form** — there's no `mem2reg` pass that would promote these slots into registers and insert `phi` nodes. `phi` exists in the opcode set and the round-trip tests, but nothing currently generates it. This is a known, stated gap (see the status table in the main README).

- **`&&` and `||` are lowered to real branches**, not a single "and" instruction. `short_circuit()` builds three blocks — one that evaluates the right-hand side, one that short-circuits without evaluating it, and one where they rejoin — because the language's semantics require that `a && b` must *not* evaluate `b` at all when `a` is false. This matters concretely: if `b` were `arr[i] > 0` and `i` were out of bounds, evaluating it anyway would trigger a trap that shouldn't happen. You can see this directly in generated IR as blocks named `^sc.rhs` and `^sc.short`.

- **`finish()`** runs at the end of every function build and guarantees every block ends in a terminator, even ones that are technically unreachable (semantic analysis already proved every real path returns, so anything left over is dead code that still needs to be syntactically valid).

## Printing and re-parsing: the round-trip guarantee

`ir/printer.py` turns the data structures back into the exact text format shown by `./provitc.py ir`. `ir/irparser.py` is the inverse: it reads that text back into the same data structures.

The property that matters here is **`parse(print(module)) == module`**, exactly — not "close enough." This is tested directly in `tests/test_frontend.py` and `experiments/exp02_ir_roundtrip.py`, including against 500 *randomly generated* modules that deliberately exercise every opcode (including ones real generated code never happens to produce, like `phi`). This matters because rules in `rulelib/` and Module B's proposals are also just text in a small DSL — if printing and parsing IR ever silently disagreed, similar bugs could easily hide in the rule pipeline without being noticed.

## See it yourself

```bash
./provitc.py ir demo/scale.mc          # see the unoptimized IR
python3 -m experiments.exp02_ir_roundtrip   # run the round-trip stress test
```
