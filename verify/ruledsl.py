"""The ProveIt-C rewrite-rule DSL.

A rule is a *generalised* rewrite: its operands and constants are symbolic, so
a single proof discharges every instance of the pattern rather than the one
instance a proposer happened to be looking at. That generality is what lets the
model run offline — the compile-time artifact is a finite rule library, and
applying it needs no inference.

Surface syntax (modelled on Alive's, deliberately narrower):

    rule mul_pow2_to_shl {
      origin textbook
      expect proven
      note   "the canonical strength reduction"
      pre    { is_pow2(C) }
      match  { %r = mul %x, C }
      rewrite{ %r = shl %x, log2(C) }
    }

Conventions
-----------
* ``%name``            an SSA register. A register that no instruction in
                       ``match`` defines is a free 32-bit input.
* ``C``, ``K``, ``N1`` a bare Capitalised identifier is a symbolic constant.
* ``42``, ``-1``       an integer literal.
* the last instruction of ``match`` and of ``rewrite`` must define the same
  register: that register is the rule's root, and the two sides are compared
  there.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ir.ir import BINARY_OPS, ICMP_PREDS

I32, I1 = "i32", "i1"

#: Unary functions available in constant expressions and preconditions.
CONST_FUNCS = {"log2", "ctz", "clz", "popcount", "abs", "width"}
#: Boolean-valued predicates available in preconditions.
PREDICATES = {"is_pow2", "is_neg_pow2", "is_all_ones", "ult", "ule", "ugt", "uge"}

SELECT_OP = "select"
NOT_OP = "not"
VALID_OPS = BINARY_OPS | {"icmp", SELECT_OP, NOT_OP}
#: operand count per opcode; everything not listed is binary
ARITY = {SELECT_OP: 3, NOT_OP: 1}


class RuleSyntaxError(Exception):
    pass


class RuleTypeError(Exception):
    pass


# --------------------------------------------------------------- tokens ----

_TOKEN_RE = re.compile(r"""
    (?P<ws>\s+)
  | (?P<comment>//[^\n]*|\#[^\n]*)
  | (?P<string>"[^"\n]*")
  | (?P<reg>%[A-Za-z_][\w.]*)
  | (?P<number>0[xX][0-9a-fA-F]+|\d+)
  | (?P<ident>[A-Za-z_]\w*)
  | (?P<op><<|>>|&&|\|\||==|!=|<=|>=|[-+*/%&|^~!<>(){},=])
""", re.VERBOSE)


@dataclass(frozen=True)
class Tok:
    kind: str
    text: str
    pos: int


def _tokenize(text: str) -> list[Tok]:
    toks: list[Tok] = []
    i = 0
    while i < len(text):
        m = _TOKEN_RE.match(text, i)
        if not m:
            raise RuleSyntaxError(f"unexpected character {text[i]!r} at offset {i}")
        kind = m.lastgroup
        if kind not in {"ws", "comment"}:
            toks.append(Tok(kind, m.group(), i))
        i = m.end()
    toks.append(Tok("eof", "", len(text)))
    return toks


# ------------------------------------------------------------ rule model ----

@dataclass
class RuleInstr:
    dst: str
    op: str
    args: list                      # expression nodes
    pred: str | None = None

    def __str__(self) -> str:
        head = f"%{self.dst} = {self.op}"
        if self.pred:
            head += f" {self.pred}"
        return head + " " + ", ".join(unparse(a) for a in self.args)


@dataclass
class Rule:
    name: str
    match: list[RuleInstr]
    rewrite: list[RuleInstr]
    pre: tuple | None = None
    origin: str = "unknown"
    #: "proven" | "refuted" | "inconclusive", asserted by tests. "inconclusive"
    #: marks a rule whose verdict is not stable across runs; only the safety
    #: half (never PROVEN) is asserted for those.
    expect: str | None = None
    note: str = ""
    types: dict[str, str] = field(default_factory=dict)
    free_vars: list[str] = field(default_factory=list)
    const_syms: list[str] = field(default_factory=list)

    @property
    def root(self) -> str:
        return self.match[-1].dst

    @property
    def root_type(self) -> str:
        return self.types[self.root]

    def render(self) -> str:
        out = [f"rule {self.name} {{", f"  origin {self.origin}"]
        if self.expect:
            out.append(f"  expect {self.expect}")
        if self.note:
            out.append(f'  note   "{self.note}"')
        if self.pre is not None:
            out.append(f"  pre    {{ {unparse(self.pre)} }}")
        out.append("  match  {")
        out += [f"    {i}" for i in self.match]
        out.append("  }")
        out.append("  rewrite {")
        out += [f"    {i}" for i in self.rewrite]
        out.append("  }")
        out.append("}")
        return "\n".join(out)


# ---------------------------------------------------------------- parser ----

class _Parser:
    def __init__(self, text: str):
        self.toks = _tokenize(text)
        self.i = 0

    @property
    def cur(self) -> Tok:
        return self.toks[self.i]

    def at(self, kind: str, text: str | None = None) -> bool:
        t = self.cur
        return t.kind == kind and (text is None or t.text == text)

    def advance(self) -> Tok:
        t = self.cur
        if t.kind != "eof":
            self.i += 1
        return t

    def accept(self, kind: str, text: str | None = None) -> Tok | None:
        return self.advance() if self.at(kind, text) else None

    def expect(self, kind: str, text: str | None = None) -> Tok:
        if self.at(kind, text):
            return self.advance()
        want = text or kind
        raise RuleSyntaxError(
            f"expected {want!r}, found {self.cur.text!r} at offset {self.cur.pos}")

    # ------------------------------------------------------------ rules ----

    def parse_file(self) -> list[Rule]:
        rules = []
        while not self.at("eof"):
            rules.append(self.parse_rule())
        return rules

    def parse_rule(self) -> Rule:
        self.expect("ident", "rule")
        name = self.expect("ident").text
        self.expect("op", "{")

        origin, expect, note = "unknown", None, ""
        pre = None
        match: list[RuleInstr] | None = None
        rewrite: list[RuleInstr] | None = None

        while not self.at("op", "}"):
            key = self.expect("ident").text
            if key == "origin":
                origin = self.expect("ident").text
            elif key == "expect":
                expect = self.expect("ident").text
                if expect not in {"proven", "refuted", "inconclusive"}:
                    raise RuleSyntaxError(
                        f"rule {name}: expect must be 'proven', 'refuted' or "
                        f"'inconclusive'")
            elif key == "note":
                note = self.expect("string").text.strip('"')
            elif key == "pre":
                self.expect("op", "{")
                pre = self.parse_bool()
                self.expect("op", "}")
            elif key == "match":
                match = self.parse_instr_block()
            elif key == "rewrite":
                rewrite = self.parse_instr_block()
            else:
                raise RuleSyntaxError(f"rule {name}: unknown section {key!r}")

        self.expect("op", "}")
        if not match or not rewrite:
            raise RuleSyntaxError(f"rule {name}: needs both match and rewrite")
        if match[-1].dst != rewrite[-1].dst:
            raise RuleSyntaxError(
                f"rule {name}: match defines %{match[-1].dst} last but rewrite "
                f"defines %{rewrite[-1].dst}; both sides must end at the same root")

        rule = Rule(name, match, rewrite, pre, origin, expect, note)
        _infer_types(rule)
        return rule

    def parse_instr_block(self) -> list[RuleInstr]:
        self.expect("op", "{")
        instrs = []
        while not self.at("op", "}"):
            instrs.append(self.parse_instr())
        self.expect("op", "}")
        if not instrs:
            raise RuleSyntaxError("empty instruction block")
        return instrs

    def parse_instr(self) -> RuleInstr:
        dst = self.expect("reg").text[1:]
        self.expect("op", "=")
        op = self.expect("ident").text
        if op not in VALID_OPS:
            raise RuleSyntaxError(
                f"unknown opcode {op!r}; expected one of {sorted(VALID_OPS)}")
        pred = None
        if op == "icmp":
            pred = self.expect("ident").text
            if pred not in ICMP_PREDS:
                raise RuleSyntaxError(f"unknown icmp predicate {pred!r}")
        args = [self.parse_const_expr()]
        while self.accept("op", ","):
            args.append(self.parse_const_expr())

        arity = ARITY.get(op, 2)
        if len(args) != arity:
            raise RuleSyntaxError(
                f"{op} takes {arity} operands, got {len(args)}")
        return RuleInstr(dst, op, args, pred)

    # ------------------------------------------------------ expressions ----

    def parse_bool(self):
        return self.parse_or()

    def parse_or(self):
        node = self.parse_and()
        while self.accept("op", "||"):
            node = ("or", node, self.parse_and())
        return node

    def parse_and(self):
        node = self.parse_not()
        while self.accept("op", "&&"):
            node = ("and", node, self.parse_not())
        return node

    def parse_not(self):
        if self.accept("op", "!"):
            return ("not", self.parse_not())
        return self.parse_bool_atom()

    def parse_bool_atom(self):
        if self.at("op", "(") and self._paren_is_boolean():
            self.expect("op", "(")
            node = self.parse_bool()
            self.expect("op", ")")
            return node
        if self.at("ident") and self.cur.text in PREDICATES:
            name = self.advance().text
            self.expect("op", "(")
            args = [self.parse_const_expr()]
            while self.accept("op", ","):
                args.append(self.parse_const_expr())
            self.expect("op", ")")
            return ("pred", name, args)
        lhs = self.parse_const_expr()
        for sym in ("==", "!=", "<=", ">=", "<", ">"):
            if self.accept("op", sym):
                return ("cmp", sym, lhs, self.parse_const_expr())
        raise RuleSyntaxError(
            f"expected a comparison or predicate at offset {self.cur.pos}")

    def _paren_is_boolean(self) -> bool:
        """Lookahead: does this parenthesised group contain a boolean operator?

        `(C)` is an arithmetic operand; `(is_pow2(C) || C == 0)` is a boolean
        group. Scanning to the matching paren is enough to tell them apart and
        keeps the precondition grammar unambiguous without a type pass.
        """
        depth = 0
        for t in self.toks[self.i:]:
            if t.kind == "op" and t.text == "(":
                depth += 1
            elif t.kind == "op" and t.text == ")":
                depth -= 1
                if depth == 0:
                    return False
            elif depth == 1 and (
                    (t.kind == "op" and t.text in {"&&", "||", "==", "!=",
                                                   "<=", ">=", "<", ">", "!"})
                    or (t.kind == "ident" and t.text in PREDICATES)):
                return True
            elif t.kind == "eof":
                break
        return False

    # Constant/operand expressions, C precedence.
    def parse_const_expr(self):
        return self._bin_level(0)

    _LEVELS = [["|"], ["^"], ["&"], ["<<", ">>"], ["+", "-"], ["*", "/", "%"]]

    def _bin_level(self, level: int):
        if level >= len(self._LEVELS):
            return self.parse_unary()
        node = self._bin_level(level + 1)
        while self.cur.kind == "op" and self.cur.text in self._LEVELS[level]:
            op = self.advance().text
            node = ("bin", op, node, self._bin_level(level + 1))
        return node

    def parse_unary(self):
        if self.at("op", "-"):
            self.advance()
            return ("un", "-", self.parse_unary())
        if self.at("op", "~"):
            self.advance()
            return ("un", "~", self.parse_unary())
        return self.parse_atom()

    def parse_atom(self):
        t = self.cur
        if t.kind == "reg":
            self.advance()
            return ("reg", t.text[1:])
        if t.kind == "number":
            self.advance()
            return ("int", int(t.text, 0))
        if t.kind == "ident":
            self.advance()
            if self.at("op", "("):
                if t.text not in CONST_FUNCS and t.text not in PREDICATES:
                    raise RuleSyntaxError(f"unknown function {t.text!r}")
                self.expect("op", "(")
                args = [self.parse_const_expr()]
                while self.accept("op", ","):
                    args.append(self.parse_const_expr())
                self.expect("op", ")")
                return ("call", t.text, args)
            if not t.text[0].isupper():
                raise RuleSyntaxError(
                    f"{t.text!r} is not a register, a literal or a symbolic "
                    f"constant; symbolic constants must start with a capital "
                    f"letter")
            return ("csym", t.text)
        if t.kind == "op" and t.text == "(":
            self.advance()
            node = self.parse_const_expr()
            self.expect("op", ")")
            return node
        raise RuleSyntaxError(f"unexpected {t.text!r} at offset {t.pos}")


# ------------------------------------------------------- type inference ----

def _operand_types(instr: RuleInstr) -> list[str]:
    if instr.op == SELECT_OP:
        return [I1, I32, I32]
    if instr.op == NOT_OP:
        return [I1]
    return [I32, I32]


def _result_type(instr: RuleInstr) -> str:
    return I1 if instr.op in {"icmp", NOT_OP} else I32


def _infer_types(rule: Rule) -> None:
    """Assign a type to every register and collect free variables/constants.

    Registers defined on the rewrite side but not the match side are legal
    (they are fresh temporaries); registers *used* on the rewrite side that
    neither side defines are not, because the rewrite would read a value the
    matcher never bound.
    """
    types: dict[str, str] = {}
    defined_match: set[str] = set()
    const_syms: list[str] = []

    def bind(reg: str, ty: str, where: str) -> None:
        prior = types.get(reg)
        if prior is not None and prior != ty:
            raise RuleTypeError(
                f"rule {rule.name}: %{reg} is used as {prior} and as {ty} "
                f"({where})")
        types[reg] = ty

    def walk_operand(node, ty: str, where: str) -> None:
        tag = node[0]
        if tag == "reg":
            bind(node[1], ty, where)
        elif tag == "csym":
            if node[1] not in const_syms:
                const_syms.append(node[1])
        elif tag in {"bin", "un", "call"}:
            for sub in (node[2:] if tag == "bin" else
                        [node[2]] if tag == "un" else node[2]):
                walk_operand(sub, I32, where)

    for side, instrs in (("match", rule.match), ("rewrite", rule.rewrite)):
        for ins in instrs:
            bind(ins.dst, _result_type(ins), f"{side}: {ins}")
            for operand, ty in zip(ins.args, _operand_types(ins)):
                walk_operand(operand, ty, f"{side}: {ins}")
            if side == "match":
                defined_match.add(ins.dst)

    # Any register the match side reads but does not define is a free input.
    free: list[str] = []
    for ins in rule.match:
        for operand in ins.args:
            for reg in _regs_in(operand):
                if reg not in defined_match and reg not in free:
                    free.append(reg)

    # The rewrite may read a free input, anything the match bound, or a
    # temporary it has already defined itself — and nothing else. Checking
    # definitions in order also rejects a rewrite that reads a temporary
    # before producing it.
    available = set(defined_match) | set(free)
    for ins in rule.rewrite:
        for operand in ins.args:
            for reg in _regs_in(operand):
                if reg not in available:
                    raise RuleTypeError(
                        f"rule {rule.name}: rewrite reads %{reg} before "
                        f"anything binds it")
        available.add(ins.dst)

    rule.types = types
    rule.free_vars = free
    rule.const_syms = const_syms


def _regs_in(node) -> list[str]:
    tag = node[0]
    if tag == "reg":
        return [node[1]]
    if tag == "bin":
        return _regs_in(node[2]) + _regs_in(node[3])
    if tag == "un":
        return _regs_in(node[2])
    if tag == "call":
        return [r for a in node[2] for r in _regs_in(a)]
    return []


# ------------------------------------------------------------- printing ----

def unparse(node) -> str:
    tag = node[0]
    if tag == "reg":
        return f"%{node[1]}"
    if tag == "int":
        return str(node[1])
    if tag == "csym":
        return node[1]
    if tag == "bin":
        return f"({unparse(node[2])} {node[1]} {unparse(node[3])})"
    if tag == "un":
        return f"{node[1]}{unparse(node[2])}"
    if tag == "call":
        return f"{node[1]}(" + ", ".join(unparse(a) for a in node[2]) + ")"
    if tag == "pred":
        return f"{node[1]}(" + ", ".join(unparse(a) for a in node[2]) + ")"
    if tag == "cmp":
        return f"{unparse(node[2])} {node[1]} {unparse(node[3])}"
    if tag == "and":
        return f"({unparse(node[1])} && {unparse(node[2])})"
    if tag == "or":
        return f"({unparse(node[1])} || {unparse(node[2])})"
    if tag == "not":
        return f"!{unparse(node[1])}"
    raise AssertionError(f"unknown node {node!r}")


def parse_rules(text: str) -> list[Rule]:
    return _Parser(text).parse_file()


def load_rules(path: str) -> list[Rule]:
    with open(path, encoding="utf-8") as fh:
        return parse_rules(fh.read())
