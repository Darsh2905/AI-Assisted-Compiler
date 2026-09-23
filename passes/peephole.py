"""Deterministic application of a proven rule library to the IR.

This module is the point of the whole project. Rule *discovery* is expensive,
offline and (eventually) model-driven; rule *application* is this file: pattern
matching, constant substitution, no inference, no network, no nondeterminism.
Two builds of the same program against the same rule-library version produce
byte-identical IR.

That separation is what distinguishes this design from the LLM-in-the-loop
compilers. Kwon et al. report 14 minutes of compile time per loop on TSVC and
26 minutes on real applications, dominated by model inference and solver calls.
Here both costs are paid once, offline, and the build pays a pattern match.

Matching is syntactic and local to a basic block. Commutative operands are
tried in both orders; nothing else is normalised, so the match rate reported by
the experiments is a floor, not a ceiling.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ir.ir import Const, Instr, Module, Reg, TRAPPING_OPS
from verify.interp import eval_bool, eval_expr
from verify.ruledsl import NOT_OP, Rule, RuleInstr, SELECT_OP

MASK = 0xFFFFFFFF
COMMUTATIVE = {"add", "mul", "and", "or", "xor"}
#: Instructions whose removal would change observable behaviour.
SIDE_EFFECTING = TRAPPING_OPS | {"store", "call", "alloca"}

#: Static cost model, v0. Deliberately crude, and the crudeness is the point:
#: a proof says a rewrite is *sound*, it says nothing about whether it is an
#: improvement. `add_split_or_and` (x + y == (x|y) + (x&y)) is proven and makes
#: every program strictly worse. Without a cost model the applier fired it 375
#: times across the benchmark suite and grew the IR by 60%.
#:
#: These numbers are ordinal placeholders, not measurements. Replacing them
#: with real per-instruction latencies is a prerequisite for any performance
#: claim; until then the only defensible metric is instruction count.
OP_COST = {
    "const": 0,
    "add": 1, "sub": 1, "and": 1, "or": 1, "xor": 1,
    "shl": 1, "ashr": 1, "lshr": 1,
    "icmp": 1, "select": 1, "not": 1,
    "mul": 3,
    "sdiv": 8, "srem": 8,
    "load": 2, "store": 2, "aload": 3, "astore": 3,
    "call": 5, "alloca": 1, "phi": 0, "br": 1, "cbr": 1, "ret": 1,
}


def side_cost(instrs) -> int:
    return sum(OP_COST.get(i.op, 1) for i in instrs)


def module_cost(module: Module) -> int:
    """Total static cost of a module under `OP_COST`.

    This, not instruction count, is what the applier is trying to reduce.
    `mul_three_to_shift_add` replaces one multiply with a shift and an add: one
    more instruction, two units less cost.
    """
    return sum(OP_COST.get(i.op, 1)
               for f in module.functions for i in f.instructions())


def is_profitable(rule: Rule) -> bool:
    """A rule is applied by default only if it *strictly* lowers cost.

    Requiring a strict decrease also removes any risk of oscillation: a pair of
    rules that rewrite into each other can never both be profitable.
    """
    return side_cost(rule.rewrite) < side_cost(rule.match)


def classify(rules: list[Rule]) -> tuple[list[Rule], list[Rule]]:
    """Split a proven library into (profitable, proven-but-not-profitable)."""
    profitable = [r for r in rules if is_profitable(r)]
    neutral = [r for r in rules if not is_profitable(r)]
    return profitable, neutral


@dataclass
class ApplyStats:
    applied: dict[str, int] = field(default_factory=dict)
    rounds: int = 0
    instrs_before: int = 0
    instrs_after: int = 0

    @property
    def total(self) -> int:
        return sum(self.applied.values())

    @property
    def reduction(self) -> float:
        if not self.instrs_before:
            return 0.0
        return 1.0 - self.instrs_after / self.instrs_before


# ------------------------------------------------------------- matching ----

class _BlockIndex:
    """Definition map for one basic block."""

    def __init__(self, instrs: list[Instr]):
        self.defs: dict[str, Instr] = {
            i.result: i for i in instrs if i.result is not None}

    def const_of(self, value) -> int | None:
        if isinstance(value, Const):
            return value.value & MASK
        if isinstance(value, Reg):
            d = self.defs.get(value.name)
            if d is not None and d.op == "const" and d.args:
                return d.args[0].value & MASK
        return None


class _Match:
    def __init__(self, rule: Rule, index: _BlockIndex):
        self.rule = rule
        self.index = index
        self.pattern_defs = {i.dst: i for i in rule.match}
        self.regs: dict[str, object] = {}
        self.consts: dict[str, int] = {}

    def root(self, ins: Instr) -> bool:
        if not self.instr(self.rule.match[-1], ins):
            return False
        if self.rule.pre is None:
            return True
        try:
            return eval_bool(self.rule.pre, self.consts)
        except KeyError:
            return False

    def instr(self, pat: RuleInstr, ins: Instr) -> bool:
        if pat.op != ins.op:
            return False
        if pat.op == "icmp" and pat.pred != ins.pred:
            return False
        if len(pat.args) != len(ins.args):
            return False

        orders = [list(ins.args)]
        if pat.op in COMMUTATIVE and len(ins.args) == 2:
            orders.append([ins.args[1], ins.args[0]])

        for order in orders:
            saved_regs, saved_consts = dict(self.regs), dict(self.consts)
            if all(self.operand(p, v) for p, v in zip(pat.args, order)):
                return True
            self.regs, self.consts = saved_regs, saved_consts
        return False

    def operand(self, node, value) -> bool:
        tag = node[0]
        if tag == "reg":
            name = node[1]
            if name in self.pattern_defs and name != self.rule.match[-1].dst:
                if not isinstance(value, Reg):
                    return False
                producer = self.index.defs.get(value.name)
                return producer is not None and self.instr(
                    self.pattern_defs[name], producer)
            prior = self.regs.get(name)
            if prior is not None:
                return prior == value
            self.regs[name] = value
            return True
        if tag == "int":
            actual = self.index.const_of(value)
            return actual is not None and actual == (node[1] & MASK)
        if tag == "csym":
            actual = self.index.const_of(value)
            if actual is None:
                return False
            prior = self.consts.get(node[1])
            if prior is not None:
                return prior == actual
            self.consts[node[1]] = actual
            return True
        # Computed operands are legal on the rewrite side only.
        return False


# ------------------------------------------------------------ rewriting ----

def _materialise(rule: Rule, m: _Match, root_result: str,
                 fresh: "_Fresh") -> list[Instr] | None:
    """Build the IR instructions the rewrite side denotes, or None if it
    cannot be expressed (e.g. a computed operand that mentions a register)."""
    temps: dict[str, Reg] = {}
    out: list[Instr] = []
    last = rule.rewrite[-1].dst

    def operand(node):
        tag = node[0]
        if tag == "reg":
            name = node[1]
            if name in temps:
                return temps[name]
            if name in m.regs:
                return m.regs[name]
            return None
        if tag == "int":
            return Const(_signed(node[1] & MASK))
        if tag == "csym":
            return Const(_signed(m.consts[node[1]]))
        try:
            return Const(_signed(eval_expr(node, m.consts)))
        except (KeyError, AssertionError):
            return None            # depends on a runtime value; not foldable

    for ins in rule.rewrite:
        args = [operand(a) for a in ins.args]
        if any(a is None for a in args):
            return None
        dst = root_result if ins.dst == last else fresh.name()
        ty = "i1" if ins.op in {"icmp", NOT_OP} else "i32"
        out.append(Instr(ins.op, args, dst, ty, pred=ins.pred))
        if ins.dst != last:
            temps[ins.dst] = Reg(dst)
    return out


def _signed(v: int) -> int:
    v &= MASK
    return v - (1 << 32) if v & (1 << 31) else v


class _Fresh:
    def __init__(self, module: Module):
        used = {i.result for f in module.functions
                for i in f.instructions() if i.result}
        self.n = 0
        self.used = used

    def name(self) -> str:
        while True:
            candidate = f"pk{self.n}"
            self.n += 1
            if candidate not in self.used:
                self.used.add(candidate)
                return candidate


# ------------------------------------------------------------- the pass ----

def apply_rules(module: Module, rules: list[Rule], max_rounds: int = 8,
                stats: ApplyStats | None = None,
                only_profitable: bool = True) -> ApplyStats:
    from ir.ir import instr_count

    if only_profitable:
        rules = [r for r in rules if is_profitable(r)]

    stats = stats or ApplyStats()
    stats.instrs_before = instr_count(module)
    fresh = _Fresh(module)

    for _ in range(max_rounds):
        changed = False
        stats.rounds += 1
        for fn in module.functions:
            for block in fn.blocks:
                i = 0
                while i < len(block.instrs):
                    ins = block.instrs[i]
                    if ins.result is None:
                        i += 1
                        continue
                    index = _BlockIndex(block.instrs)
                    for rule in rules:
                        m = _Match(rule, index)
                        if not m.root(ins):
                            continue
                        replacement = _materialise(rule, m, ins.result, fresh)
                        if replacement is None:
                            continue
                        block.instrs[i:i + 1] = replacement
                        stats.applied[rule.name] = stats.applied.get(rule.name, 0) + 1
                        changed = True
                        i += len(replacement) - 1
                        break
                    i += 1
        if not changed:
            break

    stats.instrs_after = instr_count(module)
    return stats


def dce(module: Module) -> int:
    """Remove instructions whose results are unused.

    A trapping instruction is never removed even when its result is dead: in
    MiniC a trap is observable (spec/semantics.md §3), so deleting a dead
    `sdiv` would change the program's behaviour on a division by zero. This is
    a real consequence of defining away undefined behaviour rather than a
    conservative guess.
    """
    removed = 0
    for fn in module.functions:
        changed = True
        while changed:
            changed = False
            live: set[str] = set()
            for block in fn.blocks:
                for ins in block.instrs:
                    for reg in ins.uses():
                        live.add(reg.name)
            for block in fn.blocks:
                keep = []
                for ins in block.instrs:
                    dead = (ins.result is not None
                            and ins.result not in live
                            and ins.op not in SIDE_EFFECTING
                            and not ins.is_terminator)
                    if dead:
                        removed += 1
                        changed = True
                    else:
                        keep.append(ins)
                block.instrs = keep
    return removed


def constant_fold(module: Module) -> int:
    """Fold binary operations on two constants. Trapping operands are left
    alone: folding `1 / 0` would replace a trap with a value."""
    from verify.interp import BINOPS, Trap

    folded = 0
    for fn in module.functions:
        for block in fn.blocks:
            index = _BlockIndex(block.instrs)
            for ins in block.instrs:
                if ins.op not in BINOPS or ins.result is None:
                    continue
                a = index.const_of(ins.args[0])
                b = index.const_of(ins.args[1])
                if a is None or b is None:
                    continue
                try:
                    value = BINOPS[ins.op](a, b)
                except Trap:
                    continue
                ins.op = "const"
                ins.args = [Const(_signed(value), ins.ty)]
                ins.pred = None
                folded += 1
            index = _BlockIndex(block.instrs)
    return folded


def optimize(module: Module, rules: list[Rule]) -> ApplyStats:
    """The deterministic `-O1` pipeline: fold, apply the library, fold, clean up."""
    stats = ApplyStats()
    from ir.ir import instr_count

    before = instr_count(module)
    constant_fold(module)
    apply_rules(module, rules, stats=stats)
    constant_fold(module)
    dce(module)
    stats.instrs_before = before
    stats.instrs_after = instr_count(module)
    return stats
