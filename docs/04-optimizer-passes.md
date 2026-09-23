# 4. Optimizer passes: how a proven rule actually rewrites code

**What this is for:** taking the library of already-proven rules and applying them to real IR — the only part of the whole system that runs on every compilation, with no model, no network, and no randomness involved.

**Key file:** `passes/peephole.py`

## The matching algorithm

For every instruction in every basic block, the applier (`apply_rules()`) tries every rule in the library against it, using the `_Match` class:

1. **`_Match.root()`** checks whether the instruction matches the *last* instruction of the rule's `match` pattern (the "root" — see [05-rule-dsl.md](05-rule-dsl.md) for why match/rewrite must share a root register).
2. **`_Match.instr()`** compares one pattern instruction against one real instruction: same opcode, same predicate (for `icmp`), same number of operands. If the operation is commutative (`add`, `mul`, `and`, `or`, `xor` — see the `COMMUTATIVE` set), it tries operand order both ways before giving up, since `add %x, %y` should match a pattern written as `add %y, %x` too.
3. **`_Match.operand()`** handles the interesting part: if a pattern operand is a free register (like `%x` in `mul %x, C`), the first time it's seen it just remembers what real value it matched (`self.regs[name] = value`); every *later* occurrence of that same pattern register must match the *same* real value, or the whole match fails. This is what makes `sub %x, %x` correctly refuse to match `sub %a, %b` — a rule's free variable has to mean the same thing everywhere it's used.
4. If the pattern's root instruction has a `pre` (precondition), it's evaluated using `verify/interp.py`'s expression evaluator, with whatever concrete constants got matched along the way.

If everything matches, `_materialise()` builds the actual replacement instructions from the rule's `rewrite` side, substituting in the matched registers and constants (and folding away any constant arithmetic the rewrite side computes, like `C + C`, into a literal number).

## The part that isn't obvious: a proof is not enough

`OP_COST` assigns a rough cost to each opcode (`mul` costs 3, `add` costs 1, `sdiv` costs 8, and so on — see the comment in the file for why these are placeholders, not real measurements). `is_profitable(rule)` compares the total cost of a rule's `match` side against its `rewrite` side, and **only rules that are strictly cheaper get applied by default.**

This exists because of a real bug found while building this project: a rule that's mathematically proven correct can still make every program *worse*. `add_split_or_and` (`x + y == (x|y) + (x&y)`) is a real, true identity — and it turns one instruction into three. Applying every proven rule with no cost check grew the whole benchmark suite by about 60%. **Correctness and profitability are two separate questions, checked by two separate pieces of code**, and `classify()` explicitly splits a rule library into the two groups (`profitable` and `held_back`) so this distinction is visible, not hidden.

## Other passes in this file

- **`constant_fold()`**: if both operands of a binary instruction are already known constants, replace the instruction with the computed constant directly — *unless* the operation would trap (like `1 / 0`), because folding that away would silently delete an observable trap.
- **`dce()`** (dead-code elimination): repeatedly removes any instruction whose result is never used — except instructions in `SIDE_EFFECTING` (anything that can trap, plus `store`/`call`/`alloca`), which are never removed even if their result is unused, for the same reason: a trap is real, observable behavior, not a value you can throw away.

`optimize()` ties these together into the actual pipeline used everywhere: fold constants, apply the rule library, fold again, then clean up dead code.

## See it yourself

```bash
./provitc.py ir demo/scale.mc -O       # compile with optimization on
./provitc.py library                   # list every rule and whether it's applied or held back
```
