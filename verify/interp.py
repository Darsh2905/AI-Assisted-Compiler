"""Concrete reference interpreter for MiniC rule semantics.

Deliberately written from `spec/semantics.md` rather than from
`verify/smt_encode.py`, so that the two are independent implementations of the
same specification. Agreement between them is evidence that the SMT encoding
says what the spec says; disagreement means the verifier cannot be trusted,
which is the single worst failure this project can have. The cross-check runs
in `experiments/exp04_difftest_vs_smt.py` and in the test suite.

Values are carried as unsigned 32-bit integers; `_s` reinterprets as signed.
"""

from __future__ import annotations

from verify.ruledsl import NOT_OP, Rule, RuleInstr, SELECT_OP

WIDTH = 32
MASK = (1 << WIDTH) - 1
SIGN_BIT = 1 << (WIDTH - 1)


class Trap(Exception):
    """Raised when a MiniC operation traps; caught at the fragment boundary."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _u(v: int) -> int:
    return v & MASK


def _s(v: int) -> int:
    v &= MASK
    return v - (1 << WIDTH) if v & SIGN_BIT else v


def sdiv(a: int, b: int) -> int:
    if _u(b) == 0:
        raise Trap("division by zero")
    sa, sb = _s(a), _s(b)
    q = abs(sa) // abs(sb)
    if (sa < 0) != (sb < 0):
        q = -q
    return _u(q)


def srem(a: int, b: int) -> int:
    if _u(b) == 0:
        raise Trap("remainder by zero")
    sa, sb = _s(a), _s(b)
    r = abs(sa) % abs(sb)
    return _u(-r if sa < 0 else r)


def _shift_amount(b: int) -> int:
    if _u(b) >= WIDTH:
        raise Trap("shift amount out of range")
    return _u(b)


def ctz(x: int) -> int:
    x = _u(x)
    if x == 0:
        return WIDTH
    return (x & -x).bit_length() - 1


def clz(x: int) -> int:
    x = _u(x)
    return WIDTH if x == 0 else WIDTH - x.bit_length()


def popcount(x: int) -> int:
    return bin(_u(x)).count("1")


def is_pow2(x: int) -> bool:
    x = _u(x)
    return x != 0 and (x & (x - 1)) == 0


BINOPS = {
    "add": lambda a, b: _u(a + b),
    "sub": lambda a, b: _u(a - b),
    "mul": lambda a, b: _u(a * b),
    "and": lambda a, b: _u(a & b),
    "or": lambda a, b: _u(a | b),
    "xor": lambda a, b: _u(a ^ b),
    "sdiv": sdiv,
    "srem": srem,
    "shl": lambda a, b: _u(a << _shift_amount(b)),
    "ashr": lambda a, b: _u(_s(a) >> _shift_amount(b)),
    "lshr": lambda a, b: _u(_u(a) >> _shift_amount(b)),
}

ICMP = {
    "eq": lambda a, b: _u(a) == _u(b),
    "ne": lambda a, b: _u(a) != _u(b),
    "slt": lambda a, b: _s(a) < _s(b),
    "sle": lambda a, b: _s(a) <= _s(b),
    "sgt": lambda a, b: _s(a) > _s(b),
    "sge": lambda a, b: _s(a) >= _s(b),
    "ult": lambda a, b: _u(a) < _u(b),
    "ule": lambda a, b: _u(a) <= _u(b),
    "ugt": lambda a, b: _u(a) > _u(b),
    "uge": lambda a, b: _u(a) >= _u(b),
}

_DSL_TO_OP = {"+": "add", "-": "sub", "*": "mul", "/": "sdiv", "%": "srem",
              "&": "and", "|": "or", "^": "xor", "<<": "shl", ">>": "ashr"}

_CMP = {
    "==": lambda a, b: _u(a) == _u(b),
    "!=": lambda a, b: _u(a) != _u(b),
    "<": lambda a, b: _s(a) < _s(b),
    "<=": lambda a, b: _s(a) <= _s(b),
    ">": lambda a, b: _s(a) > _s(b),
    ">=": lambda a, b: _s(a) >= _s(b),
}

_FUNCS = {
    "log2": ctz,
    "ctz": ctz,
    "clz": clz,
    "popcount": popcount,
    "abs": lambda x: _u(abs(_s(x))),
    "width": lambda _x=0: WIDTH,
}

_PREDS = {
    "is_pow2": lambda a: is_pow2(a[0]),
    "is_neg_pow2": lambda a: is_pow2(_u(-a[0])),
    "is_all_ones": lambda a: _u(a[0]) == MASK,
    "ult": lambda a: _u(a[0]) < _u(a[1]),
    "ule": lambda a: _u(a[0]) <= _u(a[1]),
    "ugt": lambda a: _u(a[0]) > _u(a[1]),
    "uge": lambda a: _u(a[0]) >= _u(a[1]),
}


def eval_expr(node, env: dict[str, int]) -> int:
    tag = node[0]
    if tag == "int":
        return _u(node[1])
    if tag == "csym":
        return _u(env[node[1]])
    if tag == "reg":
        return _u(env[node[1]])
    if tag == "un":
        v = eval_expr(node[2], env)
        return _u(-v) if node[1] == "-" else _u(~v)
    if tag == "bin":
        a = eval_expr(node[2], env)
        b = eval_expr(node[3], env)
        return BINOPS[_DSL_TO_OP[node[1]]](a, b)
    if tag == "call":
        args = [eval_expr(a, env) for a in node[2]]
        return _u(_FUNCS[node[1]](*args))
    raise AssertionError(f"cannot evaluate operand {node!r}")


def eval_bool(node, env: dict[str, int]) -> bool:
    tag = node[0]
    if tag == "and":
        return eval_bool(node[1], env) and eval_bool(node[2], env)
    if tag == "or":
        return eval_bool(node[1], env) or eval_bool(node[2], env)
    if tag == "not":
        return not eval_bool(node[1], env)
    if tag == "cmp":
        return _CMP[node[1]](eval_expr(node[2], env), eval_expr(node[3], env))
    if tag == "pred":
        return _PREDS[node[1]]([eval_expr(a, env) for a in node[2]])
    raise AssertionError(f"cannot evaluate precondition {node!r}")


def eval_instr(ins: RuleInstr, env: dict[str, int]) -> int:
    vals = [eval_expr(a, env) for a in ins.args]
    if ins.op == "icmp":
        return 1 if ICMP[ins.pred](vals[0], vals[1]) else 0
    if ins.op == SELECT_OP:
        return vals[1] if vals[0] & 1 else vals[2]
    if ins.op == NOT_OP:
        return 0 if vals[0] & 1 else 1
    return BINOPS[ins.op](vals[0], vals[1])


def eval_side(instrs: list[RuleInstr], env: dict[str, int]) -> tuple[int | None, bool]:
    """Evaluate one side of a rule. Returns `(value, trapped)`.

    On a trap the value is `None`: nothing may observe it (spec §4).
    """
    local = dict(env)
    last = None
    try:
        for ins in instrs:
            local[ins.dst] = eval_instr(ins, local)
            last = ins.dst
    except Trap:
        return None, True
    return local[last], False


def check_precondition(rule: Rule, env: dict[str, int]) -> bool:
    return True if rule.pre is None else eval_bool(rule.pre, env)


def compare_sides(rule: Rule, env: dict[str, int]) -> tuple[bool, str]:
    """Trap-preserving comparison of the two sides on one concrete assignment.

    Returns `(agree, explanation)`.
    """
    m_val, m_trap = eval_side(rule.match, env)
    r_val, r_trap = eval_side(rule.rewrite, env)
    if m_trap != r_trap:
        which = "match" if m_trap else "rewrite"
        return False, f"only the {which} side traps"
    if m_trap and r_trap:
        return True, "both sides trap"
    if m_val != r_val:
        width = rule.types.get(rule.root, "i32")
        if width == "i1":
            return False, f"match={m_val & 1} rewrite={r_val & 1}"
        return False, f"match={_s(m_val)} rewrite={_s(r_val)}"
    return True, "equal"
