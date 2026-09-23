"""Symbolic encoding of MiniC rules into QF_BV, and the equivalence check.

This is one of the two independent implementations of `spec/semantics.md`; the
other is `verify/interp.py`. Every refutation produced here is replayed through
that interpreter (`experiments/exp04_difftest_vs_smt.py`), because a verifier
that silently disagrees with the reference semantics is worse than no verifier.

The obligation discharged is *trap-preserving* equivalence:

    forall s.  trap_m(s) = trap_r(s)  and  (not trap_m(s) -> val_m(s) = val_r(s))

The solver is handed the negation. `unsat` is a proof over all 2^32 (or 2^64,
or more) inputs; `sat` yields a concrete counterexample; `unknown` is recorded
as INCONCLUSIVE and is never counted as a proof.

One failure mode is worth naming because it is invisible otherwise: a rule
whose precondition is unsatisfiable proves *vacuously* — the equivalence query
is trivially unsat because no input satisfies the premise. Such a rule can
never fire, so counting it as proven would inflate the library with dead
entries. `verify_rule` checks precondition satisfiability first and reports
VACUOUS.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import z3

from verify.ruledsl import NOT_OP, Rule, RuleInstr, SELECT_OP

WIDTH = 32

PROVEN = "PROVEN"
REFUTED = "REFUTED"
INCONCLUSIVE = "INCONCLUSIVE"
VACUOUS = "VACUOUS"
ERROR = "ERROR"


@dataclass
class Verdict:
    rule: str
    status: str
    counterexample: dict[str, int] = field(default_factory=dict)
    detail: str = ""
    solve_ms: float = 0.0

    @property
    def is_proof(self) -> bool:
        return self.status == PROVEN

    def __str__(self) -> str:
        out = f"{self.rule}: {self.status} ({self.solve_ms:.1f} ms)"
        if self.counterexample:
            out += "  counterexample " + ", ".join(
                f"{k}={_signed(v)}" for k, v in sorted(self.counterexample.items()))
        if self.detail:
            out += f"  [{self.detail}]"
        return out


def _signed(v: int) -> int:
    return v - (1 << WIDTH) if v >= (1 << (WIDTH - 1)) else v


# ------------------------------------------------------ symbolic helpers ----

def _bv(v: int, width: int = WIDTH) -> z3.BitVecRef:
    return z3.BitVecVal(v, width)


def sym_ctz(x: z3.BitVecRef) -> z3.BitVecRef:
    """Count trailing zeros; ctz(0) == 32. Built low-bit-outermost so the
    lowest set bit wins."""
    result = _bv(WIDTH)
    for i in reversed(range(WIDTH)):
        result = z3.If(z3.Extract(i, i, x) == z3.BitVecVal(1, 1), _bv(i), result)
    return result


def sym_clz(x: z3.BitVecRef) -> z3.BitVecRef:
    result = _bv(WIDTH)
    for i in range(WIDTH):
        result = z3.If(z3.Extract(i, i, x) == z3.BitVecVal(1, 1),
                       _bv(WIDTH - 1 - i), result)
    return result


def sym_popcount(x: z3.BitVecRef) -> z3.BitVecRef:
    total = _bv(0)
    for i in range(WIDTH):
        total = total + z3.ZeroExt(WIDTH - 1, z3.Extract(i, i, x))
    return total


def sym_is_pow2(x: z3.BitVecRef) -> z3.BoolRef:
    return z3.And(x != _bv(0), (x & (x - _bv(1))) == _bv(0))


def _as_i1(cond: z3.BoolRef) -> z3.BitVecRef:
    return z3.If(cond, z3.BitVecVal(1, 1), z3.BitVecVal(0, 1))


class EncodeError(Exception):
    pass


# -------------------------------------------------------------- encoder ----

class Encoder:
    """Encodes one side of a rule. `env` maps register name -> (value, trap)."""

    def __init__(self, rule: Rule, free: dict[str, z3.BitVecRef],
                 consts: dict[str, z3.BitVecRef]):
        self.rule = rule
        self.consts = consts
        self.env: dict[str, tuple[z3.BitVecRef, z3.BoolRef]] = {
            name: (val, z3.BoolVal(False)) for name, val in free.items()
        }

    def run(self, instrs: list[RuleInstr]) -> tuple[z3.BitVecRef, z3.BoolRef]:
        last = None
        for ins in instrs:
            value, trap = self.instr(ins)
            self.env[ins.dst] = (value, trap)
            last = ins.dst
        return self.env[last]

    def instr(self, ins: RuleInstr) -> tuple[z3.BitVecRef, z3.BoolRef]:
        parts = [self.expr(a) for a in ins.args]
        trap = z3.Or(*[t for _, t in parts]) if len(parts) > 1 else parts[0][1]
        vals = [v for v, _ in parts]


        if ins.op == "icmp":
            a, b = vals
            cond = _icmp(ins.pred, a, b)
            return _as_i1(cond), trap
        if ins.op == SELECT_OP:
            # `expr` hands back every operand zero-extended to 32 bits, so the
            # i1 condition is read off bit 0.
            c, a, b = vals
            return z3.If(z3.Extract(0, 0, c) == z3.BitVecVal(1, 1), a, b), trap
        if ins.op == NOT_OP:
            return _as_i1(z3.Extract(0, 0, vals[0]) == z3.BitVecVal(0, 1)), trap
        a, b = vals
        value, own_trap = _binop(ins.op, a, b)
        return value, z3.Or(trap, own_trap)

    def expr(self, node) -> tuple[z3.BitVecRef, z3.BoolRef]:
        tag = node[0]
        if tag == "int":
            return _bv(node[1] & 0xFFFFFFFF), z3.BoolVal(False)
        if tag == "csym":
            return self.consts[node[1]], z3.BoolVal(False)
        if tag == "reg":
            if node[1] not in self.env:
                raise EncodeError(f"unbound register %{node[1]}")
            value, trap = self.env[node[1]]
            if value.size() == 1:
                value = z3.ZeroExt(WIDTH - 1, value)
            return value, trap
        if tag == "un":
            v, t = self.expr(node[2])
            return (-v if node[1] == "-" else ~v), t
        if tag == "bin":
            a, ta = self.expr(node[2])
            b, tb = self.expr(node[3])
            value, own = _binop(_DSL_TO_OP[node[1]], a, b)
            return value, z3.Or(ta, tb, own)
        if tag == "call":
            args = [self.expr(a) for a in node[2]]
            trap = z3.Or(*[t for _, t in args]) if len(args) > 1 else args[0][1]
            return _const_func(node[1], [v for v, _ in args]), trap
        raise EncodeError(f"cannot encode operand node {node!r}")

    def boolean(self, node) -> z3.BoolRef:
        tag = node[0]
        if tag == "and":
            return z3.And(self.boolean(node[1]), self.boolean(node[2]))
        if tag == "or":
            return z3.Or(self.boolean(node[1]), self.boolean(node[2]))
        if tag == "not":
            return z3.Not(self.boolean(node[1]))
        if tag == "cmp":
            a, _ = self.expr(node[2])
            b, _ = self.expr(node[3])
            return _CMP[node[1]](a, b)
        if tag == "pred":
            args = [self.expr(a)[0] for a in node[2]]
            return _predicate(node[1], args)
        raise EncodeError(f"cannot encode precondition node {node!r}")


_DSL_TO_OP = {"+": "add", "-": "sub", "*": "mul", "/": "sdiv", "%": "srem",
              "&": "and", "|": "or", "^": "xor", "<<": "shl", ">>": "ashr"}

_CMP = {
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
}


def _icmp(pred: str, a, b) -> z3.BoolRef:
    return {
        "eq": lambda: a == b, "ne": lambda: a != b,
        "slt": lambda: a < b, "sle": lambda: a <= b,
        "sgt": lambda: a > b, "sge": lambda: a >= b,
        "ult": lambda: z3.ULT(a, b), "ule": lambda: z3.ULE(a, b),
        "ugt": lambda: z3.UGT(a, b), "uge": lambda: z3.UGE(a, b),
    }[pred]()


def _binop(op: str, a, b) -> tuple[z3.BitVecRef, z3.BoolRef]:
    """Returns (value, own_trap_condition) per spec/semantics.md §3-4."""
    no_trap = z3.BoolVal(False)
    if op == "add":
        return a + b, no_trap
    if op == "sub":
        return a - b, no_trap
    if op == "mul":
        return a * b, no_trap
    if op == "and":
        return a & b, no_trap
    if op == "or":
        return a | b, no_trap
    if op == "xor":
        return a ^ b, no_trap
    if op == "sdiv":
        return a / b, b == _bv(0)
    if op == "srem":
        return z3.SRem(a, b), b == _bv(0)
    if op in {"shl", "ashr", "lshr"}:
        # A negative shift amount is >= 2^31 unsigned, so UGE(b, 32) covers
        # both out-of-range cases from spec/semantics.md §3.
        trap = z3.UGE(b, _bv(WIDTH))
        value = {"shl": lambda: a << b,
                 "ashr": lambda: a >> b,
                 "lshr": lambda: z3.LShR(a, b)}[op]()
        return value, trap
    raise EncodeError(f"unknown opcode {op!r}")


def _const_func(name: str, args: list) -> z3.BitVecRef:
    x = args[0] if args else None
    if name == "log2":
        return sym_ctz(x)          # defined for powers of two, which is where it is used
    if name == "ctz":
        return sym_ctz(x)
    if name == "clz":
        return sym_clz(x)
    if name == "popcount":
        return sym_popcount(x)
    if name == "abs":
        return z3.If(x < _bv(0), -x, x)
    if name == "width":
        return _bv(WIDTH)
    raise EncodeError(f"unknown constant function {name!r}")


def _predicate(name: str, args: list) -> z3.BoolRef:
    if name == "is_pow2":
        return sym_is_pow2(args[0])
    if name == "is_neg_pow2":
        return sym_is_pow2(-args[0])
    if name == "is_all_ones":
        return args[0] == _bv(-1 & 0xFFFFFFFF)
    if name == "ult":
        return z3.ULT(args[0], args[1])
    if name == "ule":
        return z3.ULE(args[0], args[1])
    if name == "ugt":
        return z3.UGT(args[0], args[1])
    if name == "uge":
        return z3.UGE(args[0], args[1])
    raise EncodeError(f"unknown predicate {name!r}")


# ------------------------------------------------------- the entry point ----

def verify_rule(rule: Rule, timeout_ms: int = 10_000) -> Verdict:
    start = time.perf_counter()
    try:
        # An i1 free variable is declared one bit wide so the solver cannot
        # pick a "boolean" outside {0, 1}; `Encoder.expr` zero-extends on use.
        free = {name: z3.BitVec(f"free_{name}",
                                1 if rule.types.get(name) == "i1" else WIDTH)
                for name in rule.free_vars}
        consts = {name: z3.BitVec(f"const_{name}", WIDTH)
                  for name in rule.const_syms}

        pre = z3.BoolVal(True)
        if rule.pre is not None:
            pre = Encoder(rule, free, consts).boolean(rule.pre)

        # A precondition no input satisfies makes the equivalence query
        # vacuously unsat; report that rather than call it a proof.
        if rule.pre is not None:
            probe = z3.Solver()
            probe.set("timeout", timeout_ms)
            probe.add(pre)
            if probe.check() == z3.unsat:
                return Verdict(rule.name, VACUOUS,
                               detail="precondition is unsatisfiable: this rule "
                                      "can never fire",
                               solve_ms=_ms(start))

        m_val, m_trap = Encoder(rule, free, consts).run(rule.match)
        r_val, r_trap = Encoder(rule, free, consts).run(rule.rewrite)

        if m_val.size() != r_val.size():
            return Verdict(rule.name, ERROR,
                           detail=f"root width mismatch: match is i{m_val.size()},"
                                  f" rewrite is i{r_val.size()}",
                           solve_ms=_ms(start))

        solver = z3.Solver()
        solver.set("timeout", timeout_ms)
        solver.add(pre)
        solver.add(z3.Or(m_trap != r_trap,
                         z3.And(z3.Not(m_trap), z3.Not(r_trap), m_val != r_val)))

        result = solver.check()
        elapsed = _ms(start)

        if result == z3.unsat:
            return Verdict(rule.name, PROVEN, solve_ms=elapsed)
        if result == z3.sat:
            model = solver.model()
            cex = {}
            for label, table in (("", free), ("", consts)):
                for name, var in table.items():
                    value = model.eval(var, model_completion=True)
                    cex[name] = value.as_long()
            return Verdict(rule.name, REFUTED, counterexample=cex,
                           solve_ms=elapsed)
        return Verdict(rule.name, INCONCLUSIVE,
                       detail=f"solver returned unknown ({solver.reason_unknown()})",
                       solve_ms=elapsed)
    except Exception as exc:                       # noqa: BLE001 - reported, not swallowed
        return Verdict(rule.name, ERROR, detail=f"{type(exc).__name__}: {exc}",
                       solve_ms=_ms(start))


def _ms(start: float) -> float:
    return (time.perf_counter() - start) * 1000.0


def evaluate_concrete(rule: Rule, side: str,
                      env: dict[str, int]) -> tuple[int | None, bool]:
    """Evaluate one side of a rule on a concrete assignment, through the *SMT*
    encoding rather than the interpreter.

    Feeding the encoder literal bitvectors and simplifying gives a per-point
    oracle for the encoding itself. Comparing it against `verify/interp.py` on
    the same assignment is a direct test that the two implementations of
    `spec/semantics.md` agree, independent of whether any rule is equivalent.
    """
    free = {}
    for name in rule.free_vars:
        width = 1 if rule.types.get(name) == "i1" else WIDTH
        free[name] = z3.BitVecVal(env.get(name, 0), width)
    consts = {name: _bv(env.get(name, 0) & 0xFFFFFFFF)
              for name in rule.const_syms}

    instrs = rule.match if side == "match" else rule.rewrite
    value, trap = Encoder(rule, free, consts).run(instrs)
    trapped = z3.is_true(z3.simplify(trap))
    if trapped:
        return None, True
    return z3.simplify(value).as_long(), False
