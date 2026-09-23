"""Machine-readable MiniC grammar and an LL(1) analyser.

The grammar below is the single source of truth: `frontend/parser.py` is a
hand-written recursive-descent implementation of exactly these productions, and
`tests/test_grammar_ll1.py` asserts that the predictive parse table it induces
has zero conflicts.

Bodies of `if`/`else`/`while`/`for` are required to be brace-delimited blocks.
That is a deliberate language-design choice, not an oversight: it removes the
dangling-else ambiguity, which is the only reason a C-like statement grammar
needs a disambiguating rule outside the LL(1) framework.
"""

from __future__ import annotations

EPSILON = "ε"
EOF = "eof"

# Terminal names are exactly the token kinds produced by frontend/lexer.py.
TERMINALS = {
    "kw_int", "kw_bool", "kw_void", "kw_if", "kw_else", "kw_while", "kw_for",
    "kw_return", "kw_true", "kw_false",
    "intlit", "ident",
    "lparen", "rparen", "lbrace", "rbrace", "lbracket", "rbracket",
    "comma", "semi", "assign",
    "plus", "minus", "star", "slash", "percent",
    "amp", "pipe", "caret", "tilde", "bang",
    "shl", "shr",
    "lt", "le", "gt", "ge", "eq", "ne",
    "andand", "oror",
    EOF,
}

START = "Program"

# Each entry maps a nonterminal to its list of productions; a production is a
# tuple of symbols, and the empty tuple denotes an epsilon production.
GRAMMAR: dict[str, list[tuple[str, ...]]] = {
    "Program": [("FuncList", EOF)],
    "FuncList": [("FuncDecl", "FuncList"), ()],
    "FuncDecl": [("Type", "ident", "lparen", "ParamList", "rparen", "Block")],
    "ParamList": [("Param", "ParamRest"), ()],
    "ParamRest": [("comma", "Param", "ParamRest"), ()],
    "Param": [("Type", "ident", "ArrSuffix")],
    "ArrSuffix": [("lbracket", "intlit", "rbracket"), ()],
    "Type": [("kw_int",), ("kw_bool",), ("kw_void",)],

    "Block": [("lbrace", "StmtList", "rbrace")],
    "StmtList": [("Stmt", "StmtList"), ()],
    "Stmt": [
        ("VarDecl",),
        ("IfStmt",),
        ("WhileStmt",),
        ("ForStmt",),
        ("ReturnStmt",),
        ("Block",),
        ("ExprStmt",),
        ("semi",),
    ],

    "VarDecl": [("Type", "ident", "ArrSuffix", "InitOpt", "semi")],
    "InitOpt": [("assign", "Expr"), ()],

    "IfStmt": [("kw_if", "lparen", "Expr", "rparen", "Block", "ElseOpt")],
    "ElseOpt": [("kw_else", "ElseTail"), ()],
    "ElseTail": [("Block",), ("IfStmt",)],

    "WhileStmt": [("kw_while", "lparen", "Expr", "rparen", "Block")],

    "ForStmt": [(
        "kw_for", "lparen", "ForInit", "semi", "Expr", "semi", "ForStep",
        "rparen", "Block",
    )],
    "ForInit": [("Type", "ident", "InitOpt"), ("SimpleStmt",), ()],
    "ForStep": [("SimpleStmt",), ()],

    "ReturnStmt": [("kw_return", "RetOpt", "semi")],
    "RetOpt": [("Expr",), ()],

    "ExprStmt": [("SimpleStmt", "semi")],
    "SimpleStmt": [("Expr", "AssignOpt")],
    "AssignOpt": [("assign", "Expr"), ()],

    # Expressions, C precedence, left-recursion eliminated.
    "Expr": [("OrExpr",)],
    "OrExpr": [("AndExpr", "OrTail")],
    "OrTail": [("oror", "AndExpr", "OrTail"), ()],
    "AndExpr": [("BitOrExpr", "AndTail")],
    "AndTail": [("andand", "BitOrExpr", "AndTail"), ()],
    "BitOrExpr": [("BitXorExpr", "BitOrTail")],
    "BitOrTail": [("pipe", "BitXorExpr", "BitOrTail"), ()],
    "BitXorExpr": [("BitAndExpr", "BitXorTail")],
    "BitXorTail": [("caret", "BitAndExpr", "BitXorTail"), ()],
    "BitAndExpr": [("EqExpr", "BitAndTail")],
    "BitAndTail": [("amp", "EqExpr", "BitAndTail"), ()],
    "EqExpr": [("RelExpr", "EqTail")],
    "EqTail": [("EqOp", "RelExpr", "EqTail"), ()],
    "EqOp": [("eq",), ("ne",)],
    "RelExpr": [("ShiftExpr", "RelTail")],
    "RelTail": [("RelOp", "ShiftExpr", "RelTail"), ()],
    "RelOp": [("lt",), ("le",), ("gt",), ("ge",)],
    "ShiftExpr": [("AddExpr", "ShiftTail")],
    "ShiftTail": [("ShOp", "AddExpr", "ShiftTail"), ()],
    "ShOp": [("shl",), ("shr",)],
    "AddExpr": [("MulExpr", "AddTail")],
    "AddTail": [("AddOp", "MulExpr", "AddTail"), ()],
    "AddOp": [("plus",), ("minus",)],
    "MulExpr": [("Unary", "MulTail")],
    "MulTail": [("MulOp", "Unary", "MulTail"), ()],
    "MulOp": [("star",), ("slash",), ("percent",)],
    "Unary": [("UnOp", "Unary"), ("Postfix",)],
    "UnOp": [("minus",), ("bang",), ("tilde",)],
    "Postfix": [("Primary", "PostfixTail")],
    "PostfixTail": [
        ("lbracket", "Expr", "rbracket", "PostfixTail"),
        ("lparen", "ArgList", "rparen", "PostfixTail"),
        (),
    ],
    "ArgList": [("Expr", "ArgRest"), ()],
    "ArgRest": [("comma", "Expr", "ArgRest"), ()],
    "Primary": [
        ("intlit",), ("kw_true",), ("kw_false",), ("ident",),
        ("lparen", "Expr", "rparen"),
    ],
}

NONTERMINALS = set(GRAMMAR)


def _is_terminal(sym: str) -> bool:
    return sym in TERMINALS


def compute_first() -> dict[str, set[str]]:
    """FIRST sets for every nonterminal; EPSILON marks a nullable nonterminal."""
    first: dict[str, set[str]] = {nt: set() for nt in NONTERMINALS}
    changed = True
    while changed:
        changed = False
        for nt, prods in GRAMMAR.items():
            for prod in prods:
                add = first_of_sequence(prod, first)
                if not add <= first[nt]:
                    first[nt] |= add
                    changed = True
    return first


def first_of_sequence(seq: tuple[str, ...], first: dict[str, set[str]]) -> set[str]:
    """FIRST of a symbol sequence, including EPSILON if the whole sequence is nullable."""
    out: set[str] = set()
    for sym in seq:
        if _is_terminal(sym):
            out.add(sym)
            return out
        sub = first[sym]
        out |= sub - {EPSILON}
        if EPSILON not in sub:
            return out
    out.add(EPSILON)
    return out


def compute_follow(first: dict[str, set[str]]) -> dict[str, set[str]]:
    follow: dict[str, set[str]] = {nt: set() for nt in NONTERMINALS}
    follow[START].add(EOF)
    changed = True
    while changed:
        changed = False
        for nt, prods in GRAMMAR.items():
            for prod in prods:
                for i, sym in enumerate(prod):
                    if _is_terminal(sym):
                        continue
                    rest = first_of_sequence(prod[i + 1:], first)
                    add = rest - {EPSILON}
                    if EPSILON in rest or not prod[i + 1:]:
                        add |= follow[nt]
                    if not add <= follow[sym]:
                        follow[sym] |= add
                        changed = True
    return follow


def build_table() -> tuple[dict[tuple[str, str], list[tuple[str, ...]]], list[str]]:
    """Build the LL(1) predictive parse table and collect conflicts.

    A conflict is any cell holding more than one production. The second return
    value is a human-readable list of them; an empty list means the grammar is
    LL(1).
    """
    first = compute_first()
    follow = compute_follow(first)
    table: dict[tuple[str, str], list[tuple[str, ...]]] = {}
    for nt, prods in GRAMMAR.items():
        for prod in prods:
            predict = first_of_sequence(prod, first)
            terms = set(predict - {EPSILON})
            if EPSILON in predict:
                terms |= follow[nt]
            for t in terms:
                table.setdefault((nt, t), []).append(prod)

    conflicts = []
    for (nt, t), prods in sorted(table.items()):
        if len(prods) > 1:
            alts = " | ".join(" ".join(p) if p else EPSILON for p in prods)
            conflicts.append(f"{nt} on '{t}': {alts}")
    return table, conflicts


def format_sets() -> str:
    first = compute_first()
    follow = compute_follow(first)
    lines = []
    for nt in sorted(NONTERMINALS):
        f = ", ".join(sorted(first[nt]))
        fo = ", ".join(sorted(follow[nt]))
        lines.append(f"{nt}\n  FIRST  = {{{f}}}\n  FOLLOW = {{{fo}}}")
    return "\n".join(lines)


if __name__ == "__main__":
    table, conflicts = build_table()
    print(f"nonterminals={len(NONTERMINALS)} productions="
          f"{sum(len(p) for p in GRAMMAR.values())} table_cells={len(table)}")
    if conflicts:
        print(f"LL(1) CONFLICTS: {len(conflicts)}")
        for c in conflicts:
            print("  " + c)
    else:
        print("LL(1): no conflicts")
