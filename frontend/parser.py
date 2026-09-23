"""Hand-written recursive-descent parser for MiniC.

There is one method per nonterminal of `spec/grammar.py`, and the grammar is
proven conflict-free LL(1) by `tests/test_grammar_ll1.py`, so every decision
below is made on a single token of lookahead. Nothing here backtracks.

Assignment is the one place worth explaining: the grammar rule is
`SimpleStmt -> Expr AssignOpt`, so an assignment is parsed as an expression
followed by an optional `= Expr`. That keeps the *grammar* LL(1); restricting
the left side to an lvalue is a semantic check (`E0601`), not a syntactic one.
"""

from __future__ import annotations

from frontend import ast_nodes as A
from frontend.diagnostics import CompileError, Diagnostic
from frontend.lexer import INT_MAX, LexError, Span, Token, tokenize

_TYPE_TOKENS = {"kw_int": "int", "kw_bool": "bool", "kw_void": "void"}
_EXPR_START = {"intlit", "kw_true", "kw_false", "ident", "lparen",
               "minus", "bang", "tilde"}

_EQ_OPS = {"eq": "==", "ne": "!="}
_REL_OPS = {"lt": "<", "le": "<=", "gt": ">", "ge": ">="}
_SH_OPS = {"shl": "<<", "shr": ">>"}
_ADD_OPS = {"plus": "+", "minus": "-"}
_MUL_OPS = {"star": "*", "slash": "/", "percent": "%"}
_UN_OPS = {"minus": "-", "bang": "!", "tilde": "~"}


class Parser:
    def __init__(self, src: str, filename: str = "<input>"):
        self.src = src
        self.filename = filename
        self.diagnostics: list[Diagnostic] = []
        try:
            self.toks: list[Token] = tokenize(src)
        except LexError as e:
            code = ("E0003" if "comment" in e.message
                    else "E0004" if "integer" in e.message else "E0002")
            raise CompileError([Diagnostic(code, e.span, e.message)]) from None
        self.pos = 0

    # ------------------------------------------------------- utilities ----

    @property
    def cur(self) -> Token:
        return self.toks[self.pos]

    def at(self, *kinds: str) -> bool:
        return self.cur.kind in kinds

    def advance(self) -> Token:
        tok = self.cur
        if tok.kind != "eof":
            self.pos += 1
        return tok

    def accept(self, kind: str) -> Token | None:
        return self.advance() if self.cur.kind == kind else None

    def expect(self, kind: str, what: str | None = None) -> Token:
        if self.cur.kind == kind:
            return self.advance()
        want = what or f"'{_display(kind)}'"
        raise _Bail(Diagnostic(
            "E0001", self.cur.span,
            f"expected {want}, found {_describe(self.cur)}"))

    def span_from(self, start: Span) -> Span:
        prev = self.toks[max(0, self.pos - 1)].span
        end = prev.col_end if prev.line == start.line else start.col_end
        return Span(start.line, start.col_start, max(end, start.col_end), start.offset)

    # ------------------------------------------------------ entry point ----

    def parse_program(self) -> A.Program:
        start = self.cur.span
        funcs: list[A.FuncDecl] = []
        while not self.at("eof"):
            try:
                funcs.append(self.func_decl())
            except _Bail as b:
                self.diagnostics.append(b.diag)
                if not self._recover_to_top_level():
                    break
        if self.diagnostics:
            raise CompileError(self.diagnostics)
        return A.Program(self.span_from(start), funcs)

    def _recover_to_top_level(self) -> bool:
        """Panic-mode recovery: skip to the next plausible declaration start."""
        depth = 0
        while not self.at("eof"):
            if self.at("lbrace"):
                depth += 1
            elif self.at("rbrace"):
                depth -= 1
                self.advance()
                if depth <= 0:
                    return True
                continue
            elif depth == 0 and self.at(*_TYPE_TOKENS):
                return True
            self.advance()
        return False

    # ------------------------------------------------------ declarations ----

    def type_(self) -> A.Type:
        tok = self.cur
        if tok.kind not in _TYPE_TOKENS:
            raise _Bail(Diagnostic(
                "E0001", tok.span,
                f"expected a type, found {_describe(tok)}"))
        self.advance()
        return A.Type(_TYPE_TOKENS[tok.kind])

    def arr_suffix(self, base: A.Type) -> A.Type:
        if not self.accept("lbracket"):
            return base
        size_tok = self.expect("intlit", "an array size")
        self.expect("rbracket")
        if size_tok.value is None or size_tok.value <= 0:
            raise _Bail(Diagnostic(
                "E0503", size_tok.span,
                f"array size must be a positive integer literal, found {size_tok.text}"))
        return A.Type(base.name, size_tok.value)

    def func_decl(self) -> A.FuncDecl:
        start = self.cur.span
        ret = self.type_()
        name_tok = self.expect("ident", "a function name")
        self.expect("lparen")
        params = self.param_list()
        self.expect("rparen")
        body = self.block()
        return A.FuncDecl(self.span_from(start), ret, name_tok.text, params,
                          body, name_tok.span)

    def param_list(self) -> list[A.Param]:
        if self.at("rparen"):
            return []
        params = [self.param()]
        while self.accept("comma"):
            params.append(self.param())
        return params

    def param(self) -> A.Param:
        start = self.cur.span
        base = self.type_()
        name_tok = self.expect("ident", "a parameter name")
        ty = self.arr_suffix(base)
        return A.Param(self.span_from(start), ty, name_tok.text)

    # ------------------------------------------------------- statements ----

    def block(self) -> A.Block:
        start = self.cur.span
        self.expect("lbrace")
        stmts: list[A.Stmt] = []
        while not self.at("rbrace", "eof"):
            try:
                stmts.append(self.stmt())
            except _Bail as b:
                self.diagnostics.append(b.diag)
                if not self._recover_in_block():
                    break
        self.expect("rbrace")
        return A.Block(self.span_from(start), stmts)

    def _recover_in_block(self) -> bool:
        depth = 0
        while not self.at("eof"):
            if self.at("semi") and depth == 0:
                self.advance()
                return True
            if self.at("lbrace"):
                depth += 1
            elif self.at("rbrace"):
                if depth == 0:
                    return True
                depth -= 1
            self.advance()
        return False

    def stmt(self) -> A.Stmt:
        if self.at(*_TYPE_TOKENS):
            return self.var_decl()
        if self.at("kw_if"):
            return self.if_stmt()
        if self.at("kw_while"):
            return self.while_stmt()
        if self.at("kw_for"):
            return self.for_stmt()
        if self.at("kw_return"):
            return self.return_stmt()
        if self.at("lbrace"):
            return self.block()
        if self.at("semi"):
            tok = self.advance()
            return A.Empty(tok.span)
        if self.at(*_EXPR_START):
            start = self.cur.span
            s = self.simple_stmt()
            self.expect("semi")
            s.span = self.span_from(start)
            return s
        raise _Bail(Diagnostic(
            "E0001", self.cur.span,
            f"expected a statement, found {_describe(self.cur)}"))

    def var_decl(self) -> A.VarDecl:
        start = self.cur.span
        base = self.type_()
        name_tok = self.expect("ident", "a variable name")
        ty = self.arr_suffix(base)
        init = self.expr() if self.accept("assign") else None
        self.expect("semi")
        return A.VarDecl(self.span_from(start), ty, name_tok.text, init,
                         name_tok.span)

    def if_stmt(self) -> A.If:
        start = self.cur.span
        self.expect("kw_if")
        self.expect("lparen")
        cond = self.expr()
        self.expect("rparen")
        then_body = self.block()
        else_body: A.Stmt | None = None
        if self.accept("kw_else"):
            # ElseTail -> Block | IfStmt
            else_body = self.if_stmt() if self.at("kw_if") else self.block()
        return A.If(self.span_from(start), cond, then_body, else_body)

    def while_stmt(self) -> A.While:
        start = self.cur.span
        self.expect("kw_while")
        self.expect("lparen")
        cond = self.expr()
        self.expect("rparen")
        return A.While(self.span_from(start), cond, self.block())

    def for_stmt(self) -> A.For:
        start = self.cur.span
        self.expect("kw_for")
        self.expect("lparen")

        init: A.Stmt | None = None
        if self.at(*_TYPE_TOKENS):
            d_start = self.cur.span
            base = self.type_()
            name_tok = self.expect("ident", "a loop variable name")
            value = self.expr() if self.accept("assign") else None
            init = A.VarDecl(self.span_from(d_start), base, name_tok.text,
                             value, name_tok.span)
        elif self.at(*_EXPR_START):
            init = self.simple_stmt()
        self.expect("semi")

        cond = self.expr()
        self.expect("semi")

        step = self.simple_stmt() if self.at(*_EXPR_START) else None
        self.expect("rparen")
        return A.For(self.span_from(start), init, cond, step, self.block())

    def return_stmt(self) -> A.Return:
        start = self.cur.span
        self.expect("kw_return")
        value = self.expr() if self.at(*_EXPR_START) else None
        self.expect("semi")
        return A.Return(self.span_from(start), value)

    def simple_stmt(self) -> A.Stmt:
        start = self.cur.span
        lhs = self.expr()
        if self.accept("assign"):
            rhs = self.expr()
            return A.Assign(self.span_from(start), lhs, rhs)
        return A.ExprStmt(self.span_from(start), lhs)

    # ------------------------------------------------------ expressions ----

    def expr(self) -> A.Expr:
        return self.or_expr()

    def _left_assoc(self, sub, ops: dict[str, str]):
        start = self.cur.span
        node = sub()
        while self.cur.kind in ops:
            op = ops[self.advance().kind]
            rhs = sub()
            node = A.Binary(self.span_from(start), op, node, rhs)
        return node

    def or_expr(self):
        return self._left_assoc(self.and_expr, {"oror": "||"})

    def and_expr(self):
        return self._left_assoc(self.bitor_expr, {"andand": "&&"})

    def bitor_expr(self):
        return self._left_assoc(self.bitxor_expr, {"pipe": "|"})

    def bitxor_expr(self):
        return self._left_assoc(self.bitand_expr, {"caret": "^"})

    def bitand_expr(self):
        return self._left_assoc(self.eq_expr, {"amp": "&"})

    def eq_expr(self):
        return self._left_assoc(self.rel_expr, _EQ_OPS)

    def rel_expr(self):
        return self._left_assoc(self.shift_expr, _REL_OPS)

    def shift_expr(self):
        return self._left_assoc(self.add_expr, _SH_OPS)

    def add_expr(self):
        return self._left_assoc(self.mul_expr, _ADD_OPS)

    def mul_expr(self):
        return self._left_assoc(self.unary, _MUL_OPS)

    def unary(self) -> A.Expr:
        if self.cur.kind in _UN_OPS:
            start = self.cur.span
            op = _UN_OPS[self.advance().kind]
            operand = self.unary()
            # `-2147483648` is the only way to write INT_MIN; the lexer lets the
            # magnitude through and the fold happens here.
            if op == "-" and isinstance(operand, A.IntLit) and operand.value == INT_MAX + 1:
                return A.IntLit(self.span_from(start), -(INT_MAX + 1))
            return A.Unary(self.span_from(start), op, operand)
        return self.postfix()

    def postfix(self) -> A.Expr:
        start = self.cur.span
        node = self.primary()
        while True:
            if self.accept("lbracket"):
                idx = self.expr()
                self.expect("rbracket")
                node = A.Index(self.span_from(start), node, idx)
            elif self.accept("lparen"):
                args = self.arg_list()
                self.expect("rparen")
                node = A.Call(self.span_from(start), node, args)
            else:
                return node

    def arg_list(self) -> list[A.Expr]:
        if self.at("rparen"):
            return []
        args = [self.expr()]
        while self.accept("comma"):
            args.append(self.expr())
        return args

    def primary(self) -> A.Expr:
        tok = self.cur
        if tok.kind == "intlit":
            self.advance()
            return A.IntLit(tok.span, tok.value)
        if tok.kind == "kw_true":
            self.advance()
            return A.BoolLit(tok.span, True)
        if tok.kind == "kw_false":
            self.advance()
            return A.BoolLit(tok.span, False)
        if tok.kind == "ident":
            self.advance()
            return A.VarRef(tok.span, tok.text)
        if tok.kind == "lparen":
            self.advance()
            inner = self.expr()
            self.expect("rparen")
            return inner
        raise _Bail(Diagnostic(
            "E0001", tok.span,
            f"expected an expression, found {_describe(tok)}"))


class _Bail(Exception):
    """Internal: unwind to the nearest recovery point."""

    def __init__(self, diag: Diagnostic):
        super().__init__(diag.message)
        self.diag = diag


_DISPLAY = {
    "lparen": "(", "rparen": ")", "lbrace": "{", "rbrace": "}",
    "lbracket": "[", "rbracket": "]", "comma": ",", "semi": ";",
    "assign": "=", "eof": "end of file",
}


def _display(kind: str) -> str:
    return _DISPLAY.get(kind, kind)


def _describe(tok: Token) -> str:
    if tok.kind == "eof":
        return "end of file"
    return f"'{tok.text}'"


def parse(src: str, filename: str = "<input>") -> A.Program:
    return Parser(src, filename).parse_program()
