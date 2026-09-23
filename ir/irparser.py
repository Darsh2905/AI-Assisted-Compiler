"""Parser for the textual IR, the inverse of `ir/printer.py`."""

from __future__ import annotations

import re

from ir.ir import Block, Function, Instr, Module, Reg, Const, BINARY_OPS, ICMP_PREDS

# Register and block names are `[\w.]+`: irgen produces numeric temporaries
# (`%0`), dotted parameter slots (`%arg.n`) and dotted labels (`^sc.rhs`).
_FUNC = re.compile(r"^func @([A-Za-z_]\w*)\((.*)\)\s*->\s*(\w+)\s*\{$")
_LABEL = re.compile(r"^\^([\w.]+):$")
_ASSIGN = re.compile(r"^%([\w.]+)\s*=\s*(.*)$")
_PHI_PAIR = re.compile(r"\[\s*(%[\w.]+|-?\d+)\s*,\s*\^([\w.]+)\s*\]")
_CALL = re.compile(r"^call\s+(\w+)\s+@([A-Za-z_]\w*)\((.*)\)$")


class IRParseError(Exception):
    def __init__(self, message: str, lineno: int, text: str):
        super().__init__(f"line {lineno}: {message}: {text!r}")
        self.lineno = lineno


def parse_value(tok: str):
    tok = tok.strip()
    if tok.startswith("%"):
        return Reg(tok[1:])
    try:
        return Const(int(tok, 0))
    except ValueError:
        raise ValueError(f"not a value: {tok!r}") from None


def _split_args(text: str) -> list[str]:
    return [p.strip() for p in text.split(",") if p.strip()]


def parse_module(text: str) -> Module:
    module = Module()
    fn: Function | None = None
    blk: Block | None = None

    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith(";"):
            continue

        if line == "}":
            if fn is None:
                raise IRParseError("unexpected '}'", lineno, raw)
            module.functions.append(fn)
            fn, blk = None, None
            continue

        m = _FUNC.match(line)
        if m:
            if fn is not None:
                raise IRParseError("nested function", lineno, raw)
            name, params_text, ret = m.groups()
            params = []
            for p in _split_args(params_text):
                parts = p.split()
                if len(parts) != 2 or not parts[1].startswith("%"):
                    raise IRParseError("bad parameter", lineno, raw)
                params.append((parts[1][1:], parts[0]))
            fn = Function(name, params, ret)
            blk = None
            continue

        if fn is None:
            raise IRParseError("instruction outside a function", lineno, raw)

        m = _LABEL.match(line)
        if m:
            blk = Block(m.group(1))
            fn.blocks.append(blk)
            continue

        if blk is None:
            raise IRParseError("instruction outside a block", lineno, raw)

        try:
            blk.instrs.append(_parse_instr(line))
        except (ValueError, IndexError) as e:
            raise IRParseError(str(e), lineno, raw) from None

    if fn is not None:
        raise IRParseError("unterminated function", len(text.splitlines()), "")
    return module


def _parse_instr(line: str) -> Instr:
    result = None
    m = _ASSIGN.match(line)
    if m:
        result, line = m.group(1), m.group(2).strip()

    head, _, rest = line.partition(" ")
    rest = rest.strip()

    if head == "const":
        ty, _, val = rest.partition(" ")
        return Instr("const", [Const(int(val.strip(), 0), ty)], result, ty)

    if head == "icmp":
        pred, _, operands = rest.partition(" ")
        if pred not in ICMP_PREDS:
            raise ValueError(f"unknown icmp predicate {pred!r}")
        a = [parse_value(x) for x in _split_args(operands)]
        return Instr("icmp", a, result, "i1", pred=pred)

    if head == "alloca":
        ty, _, size = rest.partition(" ")
        return Instr("alloca", [], result, ty, size=int(size.strip()))

    if head == "load":
        ty, _, ptr = rest.partition(" ")
        return Instr("load", [parse_value(ptr)], result, ty)

    if head == "store":
        a = [parse_value(x) for x in _split_args(rest)]
        return Instr("store", a, None)

    if head == "aload":
        ty, _, operands = rest.partition(" ")
        a = [parse_value(x) for x in _split_args(operands)]
        return Instr("aload", a, result, ty)

    if head == "astore":
        a = [parse_value(x) for x in _split_args(rest)]
        return Instr("astore", a, None)

    if head == "call":
        m = _CALL.match(line)
        if not m:
            raise ValueError("malformed call")
        ty, callee, args_text = m.groups()
        a = [parse_value(x) for x in _split_args(args_text)]
        return Instr("call", a, result, ty, callee=callee)

    if head == "phi":
        ty, _, pairs_text = rest.partition(" ")
        pairs = _PHI_PAIR.findall(pairs_text)
        if not pairs:
            raise ValueError("phi with no incoming values")
        return Instr("phi", [parse_value(v) for v, _ in pairs], result, ty,
                     labels=[lbl for _, lbl in pairs])

    if head == "br":
        return Instr("br", [], None, labels=[rest.lstrip("^")])

    if head == "cbr":
        parts = _split_args(rest)
        if len(parts) != 3:
            raise ValueError("cbr needs a condition and two labels")
        return Instr("cbr", [parse_value(parts[0])], None,
                     labels=[parts[1].lstrip("^"), parts[2].lstrip("^")])

    if head == "ret":
        return Instr("ret", [parse_value(rest)] if rest else [], None)

    if head in BINARY_OPS:
        a = [parse_value(x) for x in _split_args(rest)]
        if len(a) != 2:
            raise ValueError(f"{head} needs two operands")
        return Instr(head, a, result, "i32")

    raise ValueError(f"unknown opcode {head!r}")
