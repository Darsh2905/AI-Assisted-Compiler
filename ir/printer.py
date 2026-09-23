"""Textual form of the IR.

`parse_module(print_module(m))` must reproduce `m` exactly; that property is
asserted over every benchmark program and over randomly generated modules in
`tests/test_ir_roundtrip.py`.
"""

from __future__ import annotations

from ir.ir import Block, Function, Instr, Module, Reg


def print_instr(ins: Instr) -> str:
    dst = f"%{ins.result} = " if ins.result else ""
    a = [str(x) for x in ins.args]

    if ins.op == "const":
        return f"{dst}const {ins.ty} {a[0]}"
    if ins.op == "icmp":
        return f"{dst}icmp {ins.pred} {a[0]}, {a[1]}"
    if ins.op == "alloca":
        return f"{dst}alloca {ins.ty} {ins.size}"
    if ins.op == "load":
        return f"{dst}load {ins.ty} {a[0]}"
    if ins.op == "store":
        return f"store {a[0]}, {a[1]}"
    if ins.op == "aload":
        return f"{dst}aload {ins.ty} {a[0]}, {a[1]}"
    if ins.op == "astore":
        return f"astore {a[0]}, {a[1]}, {a[2]}"
    if ins.op == "call":
        return f"{dst}call {ins.ty} @{ins.callee}(" + ", ".join(a) + ")"
    if ins.op == "phi":
        pairs = ", ".join(f"[{v}, ^{lbl}]" for v, lbl in zip(a, ins.labels))
        return f"{dst}phi {ins.ty} {pairs}"
    if ins.op == "br":
        return f"br ^{ins.labels[0]}"
    if ins.op == "cbr":
        return f"cbr {a[0]}, ^{ins.labels[0]}, ^{ins.labels[1]}"
    if ins.op == "ret":
        return f"ret {a[0]}" if a else "ret"
    # Binary operations.
    return f"{dst}{ins.op} {a[0]}, {a[1]}"


def print_block(b: Block) -> str:
    lines = [f"^{b.label}:"]
    lines += [f"  {print_instr(i)}" for i in b.instrs]
    return "\n".join(lines)


def print_function(f: Function) -> str:
    params = ", ".join(f"{ty} %{name}" for name, ty in f.params)
    head = f"func @{f.name}({params}) -> {f.ret_type} {{"
    body = "\n".join(print_block(b) for b in f.blocks)
    return f"{head}\n{body}\n}}"


def print_module(m: Module) -> str:
    return "\n\n".join(print_function(f) for f in m.functions) + "\n"


def format_value(v) -> str:
    return str(v) if isinstance(v, Reg) else str(v)
