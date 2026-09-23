"""MiniC abstract syntax tree.

Every node carries the source span it was parsed from. `ty` is filled in by
`frontend/sema.py`; it is `None` until then.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from frontend.lexer import Span


@dataclass
class Node:
    span: Span


# ---------------------------------------------------------------- types ----

@dataclass(frozen=True)
class Type:
    name: str                 # "int" | "bool" | "void"
    array_size: int | None = None

    @property
    def is_array(self) -> bool:
        return self.array_size is not None

    def __str__(self) -> str:
        return f"{self.name}[{self.array_size}]" if self.is_array else self.name


INT = Type("int")
BOOL = Type("bool")
VOID = Type("void")


# ---------------------------------------------------------- expressions ----

@dataclass
class Expr(Node):
    ty: Type | None = field(default=None, init=False, compare=False)


@dataclass
class IntLit(Expr):
    value: int


@dataclass
class BoolLit(Expr):
    value: bool


@dataclass
class VarRef(Expr):
    name: str


@dataclass
class Index(Expr):
    base: Expr
    index: Expr


@dataclass
class Call(Expr):
    callee: Expr
    args: list[Expr]


@dataclass
class Unary(Expr):
    op: str                   # "-" | "!" | "~"
    operand: Expr


@dataclass
class Binary(Expr):
    op: str                   # + - * / % & | ^ << >> < <= > >= == != && ||
    lhs: Expr
    rhs: Expr


# ----------------------------------------------------------- statements ----

@dataclass
class Stmt(Node):
    pass


@dataclass
class VarDecl(Stmt):
    declared_type: Type
    name: str
    init: Expr | None
    name_span: Span


@dataclass
class Assign(Stmt):
    target: Expr
    value: Expr


@dataclass
class ExprStmt(Stmt):
    expr: Expr


@dataclass
class Block(Stmt):
    stmts: list[Stmt]


@dataclass
class If(Stmt):
    cond: Expr
    then_body: Block
    else_body: Stmt | None     # Block or a nested If (else-if chain)


@dataclass
class While(Stmt):
    cond: Expr
    body: Block


@dataclass
class For(Stmt):
    init: Stmt | None
    cond: Expr
    step: Stmt | None
    body: Block


@dataclass
class Return(Stmt):
    value: Expr | None


@dataclass
class Empty(Stmt):
    pass


# ------------------------------------------------------- top level ---------

@dataclass
class Param(Node):
    declared_type: Type
    name: str


@dataclass
class FuncDecl(Node):
    return_type: Type
    name: str
    params: list[Param]
    body: Block
    name_span: Span


@dataclass
class Program(Node):
    functions: list[FuncDecl]


BINARY_OPS = {
    "+", "-", "*", "/", "%", "&", "|", "^", "<<", ">>",
    "<", "<=", ">", ">=", "==", "!=", "&&", "||",
}
ARITH_OPS = {"+", "-", "*", "/", "%", "&", "|", "^", "<<", ">>"}
COMPARE_OPS = {"<", "<=", ">", ">="}
EQUALITY_OPS = {"==", "!="}
LOGICAL_OPS = {"&&", "||"}
