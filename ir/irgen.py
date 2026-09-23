"""Lower a type-checked MiniC AST to the ProveIt-C IR.

Locals and parameters are lowered to `alloca`/`load`/`store`. The result is
valid, executable IR but it is *not* in SSA form; the mem2reg pass that would
insert phi nodes and promote allocas is future work (see README status table).
This is stated here rather than implied because every downstream claim about
"SSA-form IR" in the plan currently over-describes what is built.

`&&` and `||` are lowered to explicit control flow, which is what makes their
short-circuit trap semantics (spec/semantics.md §4) observable in the IR rather
than an informal note about the source language.
"""

from __future__ import annotations

from frontend import ast_nodes as A
from ir.ir import Block, Const, Function, Instr, Module, Reg

_ARITH_TO_OP = {
    "+": "add", "-": "sub", "*": "mul", "/": "sdiv", "%": "srem",
    "&": "and", "|": "or", "^": "xor", "<<": "shl", ">>": "ashr",
}
_CMP_TO_PRED = {"<": "slt", "<=": "sle", ">": "sgt", ">=": "sge",
                "==": "eq", "!=": "ne"}


def ir_type(t: A.Type) -> str:
    return "i1" if t.name == "bool" else "i32"


class FunctionBuilder:
    def __init__(self, fn: A.FuncDecl):
        self.ast = fn
        self.func = Function(fn.name, [], ir_type(fn.return_type))
        self.counter = 0
        self.block_ids: dict[str, int] = {}
        self.scopes: list[dict[str, tuple[str, A.Type]]] = []
        self.current: Block | None = None

    # ----------------------------------------------------------- plumbing --

    def temp(self) -> str:
        name = str(self.counter)
        self.counter += 1
        return name

    def new_block(self, hint: str) -> Block:
        n = self.block_ids.get(hint, 0)
        self.block_ids[hint] = n + 1
        label = hint if n == 0 else f"{hint}.{n}"
        return Block(label)

    def start(self, block: Block) -> None:
        self.func.blocks.append(block)
        self.current = block

    def emit(self, ins: Instr) -> Reg | None:
        assert self.current is not None
        if self.current.terminator is not None:
            # Unreachable code after a terminator; drop it.
            return Reg(ins.result) if ins.result else None
        self.current.instrs.append(ins)
        return Reg(ins.result) if ins.result else None

    def emit_binary(self, op: str, a, b) -> Reg:
        return self.emit(Instr(op, [a, b], self.temp(), "i32"))

    def terminated(self) -> bool:
        return self.current is not None and self.current.terminator is not None

    # ------------------------------------------------------------- scopes --

    def push(self) -> None:
        self.scopes.append({})

    def pop(self) -> None:
        self.scopes.pop()

    def declare(self, name: str, slot: str, ty: A.Type) -> None:
        self.scopes[-1][name] = (slot, ty)

    def lookup(self, name: str) -> tuple[str, A.Type]:
        for scope in reversed(self.scopes):
            if name in scope:
                return scope[name]
        raise KeyError(f"unbound name {name!r} reached irgen; sema should have "
                       f"rejected this program")

    # ---------------------------------------------------------- top level --

    def build(self) -> Function:
        entry = self.new_block("entry")
        self.start(entry)
        self.push()

        for p in self.ast.params:
            reg = f"arg.{p.name}"
            self.func.params.append((reg, ir_type(p.declared_type)))
            if p.declared_type.is_array:
                # Arrays are passed by reference; the parameter *is* the slot.
                self.declare(p.name, reg, p.declared_type)
            else:
                slot = self.emit(Instr("alloca", [], self.temp(),
                                       ir_type(p.declared_type), size=1))
                self.emit(Instr("store", [slot, Reg(reg)]))
                self.declare(p.name, slot.name, p.declared_type)

        for s in self.ast.body.stmts:
            self.stmt(s)
        self.pop()

        self.finish()
        return self.func

    def finish(self) -> None:
        """Give every block a terminator.

        Sema proved that a non-void function returns on every path, so any
        block still missing a terminator here is unreachable. It still needs
        one for the IR to be well formed.
        """
        for b in self.func.blocks:
            if b.terminator is None:
                if self.func.ret_type == "void":
                    b.instrs.append(Instr("ret", []))
                else:
                    zero = self.temp()
                    b.instrs.append(Instr("const", [Const(0, self.func.ret_type)],
                                          zero, self.func.ret_type))
                    b.instrs.append(Instr("ret", [Reg(zero)]))

    # --------------------------------------------------------- statements --

    def stmt(self, s: A.Stmt) -> None:
        if self.terminated():
            return
        if isinstance(s, A.VarDecl):
            self.var_decl(s)
        elif isinstance(s, A.Assign):
            self.assign(s)
        elif isinstance(s, A.ExprStmt):
            self.expr(s.expr)
        elif isinstance(s, A.Block):
            self.push()
            for inner in s.stmts:
                self.stmt(inner)
            self.pop()
        elif isinstance(s, A.If):
            self.if_stmt(s)
        elif isinstance(s, A.While):
            self.while_stmt(s)
        elif isinstance(s, A.For):
            self.for_stmt(s)
        elif isinstance(s, A.Return):
            self.return_stmt(s)
        elif isinstance(s, A.Empty):
            pass
        else:
            raise AssertionError(f"unhandled statement {type(s).__name__}")

    def var_decl(self, s: A.VarDecl) -> None:
        ty = s.declared_type
        size = ty.array_size if ty.is_array else 1
        slot = self.emit(Instr("alloca", [], self.temp(), ir_type(ty), size=size))
        self.declare(s.name, slot.name, ty)
        if s.init is not None:
            self.emit(Instr("store", [slot, self.expr(s.init)]))
        elif ty.is_array:
            # Zero-initialised by spec; emit the stores explicitly so the IR
            # does not depend on an implicit memory model.
            zero = self.emit(Instr("const", [Const(0)], self.temp(), "i32"))
            for i in range(size):
                idx = self.emit(Instr("const", [Const(i)], self.temp(), "i32"))
                self.emit(Instr("astore", [slot, idx, zero]))
        else:
            zero = self.emit(Instr("const", [Const(0, ir_type(ty))],
                                   self.temp(), ir_type(ty)))
            self.emit(Instr("store", [slot, zero]))

    def assign(self, s: A.Assign) -> None:
        if isinstance(s.target, A.VarRef):
            slot, _ = self.lookup(s.target.name)
            self.emit(Instr("store", [Reg(slot), self.expr(s.value)]))
        elif isinstance(s.target, A.Index):
            assert isinstance(s.target.base, A.VarRef)
            slot, _ = self.lookup(s.target.base.name)
            index = self.expr(s.target.index)
            self.emit(Instr("astore", [Reg(slot), index, self.expr(s.value)]))
        else:
            raise AssertionError("sema should have rejected this assignment target")

    def if_stmt(self, s: A.If) -> None:
        cond = self.expr(s.cond)
        then_b = self.new_block("then")
        else_b = self.new_block("else") if s.else_body is not None else None
        end_b = self.new_block("endif")
        self.emit(Instr("cbr", [cond], labels=[
            then_b.label, (else_b or end_b).label]))

        self.start(then_b)
        self.push()
        for inner in s.then_body.stmts:
            self.stmt(inner)
        self.pop()
        if not self.terminated():
            self.emit(Instr("br", [], labels=[end_b.label]))

        if else_b is not None:
            self.start(else_b)
            self.push()
            if isinstance(s.else_body, A.Block):
                for inner in s.else_body.stmts:
                    self.stmt(inner)
            else:
                self.stmt(s.else_body)
            self.pop()
            if not self.terminated():
                self.emit(Instr("br", [], labels=[end_b.label]))

        self.start(end_b)

    def while_stmt(self, s: A.While) -> None:
        head = self.new_block("loop")
        body = self.new_block("body")
        end = self.new_block("endloop")
        self.emit(Instr("br", [], labels=[head.label]))

        self.start(head)
        self.emit(Instr("cbr", [self.expr(s.cond)],
                        labels=[body.label, end.label]))

        self.start(body)
        self.push()
        for inner in s.body.stmts:
            self.stmt(inner)
        self.pop()
        if not self.terminated():
            self.emit(Instr("br", [], labels=[head.label]))

        self.start(end)

    def for_stmt(self, s: A.For) -> None:
        self.push()
        if s.init is not None:
            self.stmt(s.init)
        head = self.new_block("loop")
        body = self.new_block("body")
        step = self.new_block("step")
        end = self.new_block("endloop")
        self.emit(Instr("br", [], labels=[head.label]))

        self.start(head)
        self.emit(Instr("cbr", [self.expr(s.cond)],
                        labels=[body.label, end.label]))

        self.start(body)
        self.push()
        for inner in s.body.stmts:
            self.stmt(inner)
        self.pop()
        if not self.terminated():
            self.emit(Instr("br", [], labels=[step.label]))

        self.start(step)
        if s.step is not None:
            self.stmt(s.step)
        if not self.terminated():
            self.emit(Instr("br", [], labels=[head.label]))

        self.start(end)
        self.pop()

    def return_stmt(self, s: A.Return) -> None:
        if s.value is None:
            self.emit(Instr("ret", []))
        else:
            self.emit(Instr("ret", [self.expr(s.value)]))

    # -------------------------------------------------------- expressions --

    def expr(self, e: A.Expr):
        if isinstance(e, A.IntLit):
            return self.emit(Instr("const", [Const(e.value)], self.temp(), "i32"))
        if isinstance(e, A.BoolLit):
            return self.emit(Instr("const", [Const(int(e.value), "i1")],
                                   self.temp(), "i1"))
        if isinstance(e, A.VarRef):
            slot, ty = self.lookup(e.name)
            if ty.is_array:
                return Reg(slot)
            return self.emit(Instr("load", [Reg(slot)], self.temp(), ir_type(ty)))
        if isinstance(e, A.Index):
            assert isinstance(e.base, A.VarRef)
            slot, ty = self.lookup(e.base.name)
            index = self.expr(e.index)
            return self.emit(Instr("aload", [Reg(slot), index], self.temp(),
                                   ir_type(A.Type(ty.name))))
        if isinstance(e, A.Call):
            assert isinstance(e.callee, A.VarRef)
            args = [self.expr(a) for a in e.args]
            ty = ir_type(e.ty) if e.ty is not None else "i32"
            result = None if (e.ty is not None and e.ty.name == "void") else self.temp()
            return self.emit(Instr("call", args, result, ty, callee=e.callee.name))
        if isinstance(e, A.Unary):
            return self.unary(e)
        if isinstance(e, A.Binary):
            return self.binary(e)
        raise AssertionError(f"unhandled expression {type(e).__name__}")

    def unary(self, e: A.Unary):
        v = self.expr(e.operand)
        if e.op == "-":
            zero = self.emit(Instr("const", [Const(0)], self.temp(), "i32"))
            return self.emit_binary("sub", zero, v)
        if e.op == "~":
            ones = self.emit(Instr("const", [Const(-1)], self.temp(), "i32"))
            return self.emit_binary("xor", v, ones)
        one = self.emit(Instr("const", [Const(1, "i1")], self.temp(), "i1"))
        return self.emit(Instr("xor", [v, one], self.temp(), "i1"))

    def binary(self, e: A.Binary):
        if e.op in {"&&", "||"}:
            return self.short_circuit(e)
        if e.op in _ARITH_TO_OP:
            lhs = self.expr(e.lhs)
            rhs = self.expr(e.rhs)
            return self.emit_binary(_ARITH_TO_OP[e.op], lhs, rhs)
        if e.op in _CMP_TO_PRED:
            lhs = self.expr(e.lhs)
            rhs = self.expr(e.rhs)
            return self.emit(Instr("icmp", [lhs, rhs], self.temp(), "i1",
                                   pred=_CMP_TO_PRED[e.op]))
        raise AssertionError(f"unhandled operator {e.op}")

    def short_circuit(self, e: A.Binary):
        """Lower `&&`/`||` through a one-slot temporary and real branches."""
        slot = self.emit(Instr("alloca", [], self.temp(), "i1", size=1))
        rhs_b = self.new_block("sc.rhs")
        short_b = self.new_block("sc.short")
        end_b = self.new_block("sc.end")

        lhs = self.expr(e.lhs)
        if e.op == "&&":
            self.emit(Instr("cbr", [lhs], labels=[rhs_b.label, short_b.label]))
            short_value = 0
        else:
            self.emit(Instr("cbr", [lhs], labels=[short_b.label, rhs_b.label]))
            short_value = 1

        self.start(rhs_b)
        self.emit(Instr("store", [slot, self.expr(e.rhs)]))
        if not self.terminated():
            self.emit(Instr("br", [], labels=[end_b.label]))

        self.start(short_b)
        k = self.emit(Instr("const", [Const(short_value, "i1")], self.temp(), "i1"))
        self.emit(Instr("store", [slot, k]))
        self.emit(Instr("br", [], labels=[end_b.label]))

        self.start(end_b)
        return self.emit(Instr("load", [slot], self.temp(), "i1"))


def generate(prog: A.Program) -> Module:
    return Module([FunctionBuilder(fn).build() for fn in prog.functions])
