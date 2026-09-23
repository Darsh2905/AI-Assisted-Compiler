"""Semantic analysis: scopes, types, calls, return paths.

Two passes. The first collects function signatures so that forward references
and mutual recursion type-check; the second walks each body.

Errors are accumulated, not thrown on first sight, because Module A's
experiment needs a corpus of programs with *several* faults and a diagnostic
for each. Every diagnostic that can carry `expected`/`found`/`in_scope` does.
"""

from __future__ import annotations

from dataclasses import dataclass

from frontend import ast_nodes as A
from frontend.ast_nodes import BOOL, INT, VOID
from frontend.diagnostics import CompileError, Diagnostic
from frontend.lexer import INT_MAX, INT_MIN, Span


@dataclass
class Symbol:
    name: str
    ty: A.Type
    span: Span
    kind: str = "var"          # "var" | "param"


@dataclass
class FuncSig:
    name: str
    return_type: A.Type
    params: list[A.Type]
    param_names: list[str]
    span: Span


class Scope:
    def __init__(self, parent: "Scope | None" = None):
        self.parent = parent
        self.symbols: dict[str, Symbol] = {}

    def declare(self, sym: Symbol) -> Symbol | None:
        """Return the prior symbol if this scope already binds the name."""
        prior = self.symbols.get(sym.name)
        if prior is not None:
            return prior
        self.symbols[sym.name] = sym
        return None

    def lookup(self, name: str) -> Symbol | None:
        scope: Scope | None = self
        while scope is not None:
            if name in scope.symbols:
                return scope.symbols[name]
            scope = scope.parent
        return None

    def visible(self) -> list[dict]:
        """Innermost-first list of visible bindings, for grounded diagnostics."""
        seen: set[str] = set()
        out: list[dict] = []
        scope: Scope | None = self
        while scope is not None:
            for name, sym in scope.symbols.items():
                if name not in seen:
                    seen.add(name)
                    out.append({"name": name, "type": str(sym.ty),
                                "kind": sym.kind})
            scope = scope.parent
        return out


class Analyzer:
    def __init__(self, src: str):
        self.src = src
        self.diagnostics: list[Diagnostic] = []
        self.functions: dict[str, FuncSig] = {}
        self.scope = Scope()
        self.current_fn: FuncSig | None = None

    # ---------------------------------------------------------- helpers ----

    def error(self, code: str, span: Span, message: str, *,
              expected: str | None = None, found: str | None = None,
              scoped: bool = True, notes: list[str] | None = None) -> None:
        self.diagnostics.append(Diagnostic(
            code, span, message, expected=expected, found=found,
            in_scope=self.scope.visible() if scoped else [],
            notes=notes or []))

    def push(self) -> None:
        self.scope = Scope(self.scope)

    def pop(self) -> None:
        assert self.scope.parent is not None
        self.scope = self.scope.parent

    # ------------------------------------------------------------ passes ----

    def run(self, prog: A.Program) -> A.Program:
        for fn in prog.functions:
            if fn.name in self.functions:
                prior = self.functions[fn.name]
                self.error("E0104", fn.name_span,
                           f"function '{fn.name}' is already defined",
                           scoped=False,
                           notes=[f"previous definition at line {prior.span.line}"])
                continue
            self.functions[fn.name] = FuncSig(
                fn.name, fn.return_type, [p.declared_type for p in fn.params],
                [p.name for p in fn.params], fn.name_span)

        for fn in prog.functions:
            self.check_function(fn)

        if self.diagnostics:
            raise CompileError(self.diagnostics)
        return prog

    def check_function(self, fn: A.FuncDecl) -> None:
        self.current_fn = self.functions.get(fn.name)
        self.push()
        for p in fn.params:
            if p.declared_type.name == "void":
                self.error("E0701", p.span,
                           f"parameter '{p.name}' cannot have type void")
            prior = self.scope.declare(Symbol(p.name, p.declared_type, p.span,
                                              "param"))
            if prior is not None:
                self.error("E0102", p.span,
                           f"parameter '{p.name}' is already declared",
                           notes=[f"previous declaration at line {prior.span.line}"])
        for s in fn.body.stmts:
            self.check_stmt(s)
        self.pop()

        if fn.return_type.name != "void" and not always_returns(fn.body):
            self.error("E0401", fn.name_span,
                       f"function '{fn.name}' must return {fn.return_type} "
                       f"on every path", scoped=False,
                       expected=str(fn.return_type),
                       notes=["control can reach the end of the function body"])
        self.current_fn = None

    # -------------------------------------------------------- statements ----

    def check_stmt(self, s: A.Stmt) -> None:
        if isinstance(s, A.VarDecl):
            self.check_var_decl(s)
        elif isinstance(s, A.Assign):
            self.check_assign(s)
        elif isinstance(s, A.ExprStmt):
            self.check_expr(s.expr)
        elif isinstance(s, A.Block):
            self.push()
            for inner in s.stmts:
                self.check_stmt(inner)
            self.pop()
        elif isinstance(s, A.If):
            self.check_condition(s.cond, "if")
            self.push()
            for inner in s.then_body.stmts:
                self.check_stmt(inner)
            self.pop()
            if s.else_body is not None:
                self.push()
                if isinstance(s.else_body, A.Block):
                    for inner in s.else_body.stmts:
                        self.check_stmt(inner)
                else:
                    self.check_stmt(s.else_body)
                self.pop()
        elif isinstance(s, A.While):
            self.check_condition(s.cond, "while")
            self.push()
            for inner in s.body.stmts:
                self.check_stmt(inner)
            self.pop()
        elif isinstance(s, A.For):
            self.push()
            if s.init is not None:
                self.check_stmt(s.init)
            self.check_condition(s.cond, "for")
            if s.step is not None:
                self.check_stmt(s.step)
            for inner in s.body.stmts:
                self.check_stmt(inner)
            self.pop()
        elif isinstance(s, A.Return):
            self.check_return(s)
        elif isinstance(s, A.Empty):
            pass
        else:
            raise AssertionError(f"unhandled statement {type(s).__name__}")

    def check_var_decl(self, s: A.VarDecl) -> None:
        if s.declared_type.name == "void":
            self.error("E0701", s.name_span,
                       f"variable '{s.name}' cannot have type void")
        if s.init is not None:
            ty = self.check_expr(s.init)
            if s.declared_type.is_array:
                self.error("E0201", s.init.span,
                           f"array '{s.name}' cannot be initialised from an "
                           f"expression",
                           expected=str(s.declared_type), found=str(ty))
            elif ty is not None and ty != s.declared_type:
                self.error("E0201", s.init.span,
                           f"cannot initialise '{s.name}' of type "
                           f"{s.declared_type} with a value of type {ty}",
                           expected=str(s.declared_type), found=str(ty))
        prior = self.scope.declare(Symbol(s.name, s.declared_type, s.name_span))
        if prior is not None:
            self.error("E0102", s.name_span,
                       f"variable '{s.name}' is already declared in this scope",
                       notes=[f"previous declaration at line {prior.span.line}"])

    def check_assign(self, s: A.Assign) -> None:
        target_ty = self.check_expr(s.target)
        value_ty = self.check_expr(s.value)
        if not is_lvalue(s.target):
            self.error("E0601", s.target.span,
                       "left side of an assignment must be a variable or an "
                       "array element")
            return
        if isinstance(s.target, A.VarRef) and target_ty is not None and target_ty.is_array:
            self.error("E0504", s.target.span,
                       f"cannot assign to array '{s.target.name}' as a whole",
                       expected="int or bool", found=str(target_ty))
            return
        if target_ty is not None and value_ty is not None and target_ty != value_ty:
            self.error("E0201", s.span,
                       f"cannot assign a value of type {value_ty} to a target "
                       f"of type {target_ty}",
                       expected=str(target_ty), found=str(value_ty))

    def check_condition(self, cond: A.Expr, keyword: str) -> None:
        ty = self.check_expr(cond)
        if ty is not None and ty != BOOL:
            self.error("E0203", cond.span,
                       f"the condition of '{keyword}' must be bool, not {ty}",
                       expected="bool", found=str(ty),
                       notes=["MiniC has no implicit int-to-bool conversion; "
                              "write an explicit comparison such as 'x != 0'"])

    def check_return(self, s: A.Return) -> None:
        fn = self.current_fn
        if fn is None:
            return
        if s.value is None:
            if fn.return_type.name != "void":
                self.error("E0402", s.span,
                           f"function '{fn.name}' must return "
                           f"{fn.return_type}, but this return has no value",
                           expected=str(fn.return_type), found="void")
            return
        ty = self.check_expr(s.value)
        if fn.return_type.name == "void":
            self.error("E0403", s.value.span,
                       f"function '{fn.name}' is void and cannot return a value",
                       expected="void", found=str(ty) if ty else None)
        elif ty is not None and ty != fn.return_type:
            self.error("E0402", s.value.span,
                       f"function '{fn.name}' returns {fn.return_type}, but "
                       f"this expression has type {ty}",
                       expected=str(fn.return_type), found=str(ty))

    # ------------------------------------------------------- expressions ----

    def check_expr(self, e: A.Expr) -> A.Type | None:
        ty = self._check_expr(e)
        e.ty = ty
        return ty

    def _check_expr(self, e: A.Expr) -> A.Type | None:
        if isinstance(e, A.IntLit):
            if not (INT_MIN <= e.value <= INT_MAX):
                self.error("E0204", e.span,
                           f"integer literal {e.value} does not fit in int "
                           f"[{INT_MIN}, {INT_MAX}]")
            return INT
        if isinstance(e, A.BoolLit):
            return BOOL
        if isinstance(e, A.VarRef):
            return self._check_varref(e)
        if isinstance(e, A.Index):
            return self._check_index(e)
        if isinstance(e, A.Call):
            return self._check_call(e)
        if isinstance(e, A.Unary):
            return self._check_unary(e)
        if isinstance(e, A.Binary):
            return self._check_binary(e)
        raise AssertionError(f"unhandled expression {type(e).__name__}")

    def _check_varref(self, e: A.VarRef) -> A.Type | None:
        sym = self.scope.lookup(e.name)
        if sym is None:
            if e.name in self.functions:
                return None          # bare function name; _check_call handles it
            self.error("E0101", e.span, f"undefined variable '{e.name}'",
                       found=e.name,
                       notes=_suggest(e.name, self.scope.visible()))
            return None
        return sym.ty

    def _check_index(self, e: A.Index) -> A.Type | None:
        base_ty = self.check_expr(e.base)
        index_ty = self.check_expr(e.index)
        if index_ty is not None and index_ty != INT:
            self.error("E0501", e.index.span,
                       f"array index must be int, not {index_ty}",
                       expected="int", found=str(index_ty))
        if base_ty is None:
            return None
        if not base_ty.is_array:
            self.error("E0502", e.base.span,
                       f"cannot index a value of type {base_ty}",
                       expected="an array type", found=str(base_ty))
            return None
        return A.Type(base_ty.name)

    def _check_call(self, e: A.Call) -> A.Type | None:
        if not isinstance(e.callee, A.VarRef):
            self.error("E0602", e.callee.span,
                       "only a named function can be called")
            for a in e.args:
                self.check_expr(a)
            return None
        name = e.callee.name
        sig = self.functions.get(name)
        if sig is None:
            for a in e.args:
                self.check_expr(a)
            self.error("E0103", e.callee.span, f"undefined function '{name}'",
                       found=name,
                       notes=_suggest(name, [{"name": f} for f in self.functions]))
            return None
        arg_types = [self.check_expr(a) for a in e.args]
        if len(arg_types) != len(sig.params):
            self.error("E0301", e.span,
                       f"function '{name}' takes {len(sig.params)} argument(s), "
                       f"but {len(arg_types)} were given",
                       expected=f"{len(sig.params)} arguments",
                       found=f"{len(arg_types)} arguments",
                       notes=[f"'{name}' is declared as "
                              f"{sig.return_type} {name}("
                              + ", ".join(f"{t} {n}" for t, n in
                                          zip(sig.params, sig.param_names))
                              + ")"])
        else:
            for i, (want, got) in enumerate(zip(sig.params, arg_types)):
                if got is not None and want != got:
                    self.error("E0302", e.args[i].span,
                               f"argument {i + 1} of '{name}' has type {want}, "
                               f"but this expression has type {got}",
                               expected=str(want), found=str(got))
        return sig.return_type

    def _check_unary(self, e: A.Unary) -> A.Type | None:
        ty = self.check_expr(e.operand)
        if ty is None:
            return None
        if e.op in {"-", "~"}:
            if ty != INT:
                self.error("E0202", e.span,
                           f"unary '{e.op}' expects int, not {ty}",
                           expected="int", found=str(ty))
                return INT
            return INT
        if ty != BOOL:
            self.error("E0202", e.span,
                       f"unary '!' expects bool, not {ty}",
                       expected="bool", found=str(ty))
        return BOOL

    def _check_binary(self, e: A.Binary) -> A.Type | None:
        lhs = self.check_expr(e.lhs)
        rhs = self.check_expr(e.rhs)
        op = e.op

        if op in A.ARITH_OPS:
            for side, ty in (("left", lhs), ("right", rhs)):
                if ty is not None and ty != INT:
                    self.error("E0202", e.span,
                               f"the {side} operand of '{op}' must be int, "
                               f"not {ty}",
                               expected="int", found=str(ty))
            return INT
        if op in A.COMPARE_OPS:
            for side, ty in (("left", lhs), ("right", rhs)):
                if ty is not None and ty != INT:
                    self.error("E0202", e.span,
                               f"the {side} operand of '{op}' must be int, "
                               f"not {ty}",
                               expected="int", found=str(ty))
            return BOOL
        if op in A.EQUALITY_OPS:
            if lhs is not None and rhs is not None:
                if lhs.is_array or rhs.is_array:
                    self.error("E0504", e.span,
                               f"'{op}' cannot compare arrays",
                               expected="int or bool",
                               found=f"{lhs} and {rhs}")
                elif lhs != rhs:
                    self.error("E0202", e.span,
                               f"'{op}' compares {lhs} with {rhs}",
                               expected=str(lhs), found=str(rhs))
            return BOOL
        if op in A.LOGICAL_OPS:
            for side, ty in (("left", lhs), ("right", rhs)):
                if ty is not None and ty != BOOL:
                    self.error("E0202", e.span,
                               f"the {side} operand of '{op}' must be bool, "
                               f"not {ty}",
                               expected="bool", found=str(ty))
            return BOOL
        raise AssertionError(f"unhandled operator {op}")


def is_lvalue(e: A.Expr) -> bool:
    return isinstance(e, A.VarRef) or (
        isinstance(e, A.Index) and isinstance(e.base, A.VarRef))


def always_returns(s: A.Stmt) -> bool:
    """Conservative: a loop is never assumed to execute."""
    if isinstance(s, A.Return):
        return True
    if isinstance(s, A.Block):
        return any(always_returns(x) for x in s.stmts)
    if isinstance(s, A.If):
        return (s.else_body is not None
                and always_returns(s.then_body)
                and always_returns(s.else_body))
    return False


def _suggest(name: str, candidates: list[dict]) -> list[str]:
    """Cheap edit-distance-1 suggestion; deterministic, no model involved."""
    close = [c["name"] for c in candidates
             if c["name"] != name and _within_one_edit(name, c["name"])]
    return [f"a similar name is in scope: '{close[0]}'"] if close else []


def _within_one_edit(a: str, b: str) -> bool:
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) == 1
    short, long = (a, b) if len(a) < len(b) else (b, a)
    for i in range(len(long)):
        if long[:i] + long[i + 1:] == short:
            return True
    return False


def analyze(prog: A.Program, src: str) -> A.Program:
    return Analyzer(src).run(prog)
