"""ProveIt-C intermediate representation.

Three-address, basic-block structured, with an explicit textual form that
round-trips (print -> parse -> identical text). The round trip matters more
than it looks: the rule DSL, the miner's prompts and every saved rule are text,
so a printer/parser pair that disagrees would silently corrupt the rule library.

The opcode set is deliberately small (~20). A small IR keeps the SMT encoding
in `verify/smt_encode.py` tractable and keeps proposer prompts short.

`phi` is part of the representation, but `ir/irgen.py` does not yet emit it:
locals are lowered to `alloca`/`load`/`store`, and the mem2reg pass that would
put a function into SSA form is not implemented. See README status table.
"""

from __future__ import annotations

from dataclasses import dataclass, field

I32 = "i32"
I1 = "i1"
PTR = "ptr"

BINARY_OPS = {
    "add", "sub", "mul", "sdiv", "srem",
    "and", "or", "xor", "shl", "ashr", "lshr",
}
#: Operations that can trap, and the condition under which they do.
TRAPPING_OPS = {"sdiv", "srem", "shl", "ashr", "lshr", "aload", "astore"}
ICMP_PREDS = {"eq", "ne", "slt", "sle", "sgt", "sge", "ult", "ule", "ugt", "uge"}
TERMINATORS = {"br", "cbr", "ret"}


@dataclass(frozen=True)
class Reg:
    name: str

    def __str__(self) -> str:
        return f"%{self.name}"


@dataclass(frozen=True)
class Const:
    value: int
    ty: str = I32

    def __str__(self) -> str:
        return str(self.value)


Value = Reg | Const


@dataclass
class Instr:
    op: str
    args: list[Value] = field(default_factory=list)
    result: str | None = None
    ty: str = I32                       # type of `result`
    pred: str | None = None             # icmp predicate
    callee: str | None = None           # call target
    size: int | None = None             # alloca element count
    labels: list[str] = field(default_factory=list)   # br / cbr / phi blocks

    @property
    def is_terminator(self) -> bool:
        return self.op in TERMINATORS

    def uses(self) -> list[Reg]:
        return [a for a in self.args if isinstance(a, Reg)]


@dataclass
class Block:
    label: str
    instrs: list[Instr] = field(default_factory=list)

    @property
    def terminator(self) -> Instr | None:
        return self.instrs[-1] if self.instrs and self.instrs[-1].is_terminator else None

    def successors(self) -> list[str]:
        t = self.terminator
        return list(t.labels) if t is not None and t.op in {"br", "cbr"} else []


@dataclass
class Function:
    name: str
    params: list[tuple[str, str]]       # (register name, type)
    ret_type: str
    blocks: list[Block] = field(default_factory=list)

    def block(self, label: str) -> Block | None:
        return next((b for b in self.blocks if b.label == label), None)

    @property
    def entry(self) -> Block | None:
        return self.blocks[0] if self.blocks else None

    def instructions(self):
        for b in self.blocks:
            yield from b.instrs


@dataclass
class Module:
    functions: list[Function] = field(default_factory=list)

    def function(self, name: str) -> Function | None:
        return next((f for f in self.functions if f.name == name), None)


def instr_count(module: Module) -> int:
    return sum(1 for f in module.functions for _ in f.instructions())
