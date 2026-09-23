"""MiniC lexer.

Every token carries a source span. Spans are the substrate for Module A
(grounded diagnostics): a diagnostic that cannot point at a byte range cannot
be turned into a machine-checkable patch.
"""

from __future__ import annotations

from dataclasses import dataclass

KEYWORDS = {
    "int": "kw_int",
    "bool": "kw_bool",
    "void": "kw_void",
    "if": "kw_if",
    "else": "kw_else",
    "while": "kw_while",
    "for": "kw_for",
    "return": "kw_return",
    "true": "kw_true",
    "false": "kw_false",
}

# Longest match first, so `<<` beats `<` and `<=`.
OPERATORS = [
    ("<<", "shl"), (">>", "shr"),
    ("<=", "le"), (">=", "ge"), ("==", "eq"), ("!=", "ne"),
    ("&&", "andand"), ("||", "oror"),
    ("(", "lparen"), (")", "rparen"), ("{", "lbrace"), ("}", "rbrace"),
    ("[", "lbracket"), ("]", "rbracket"),
    (",", "comma"), (";", "semi"), ("=", "assign"),
    ("+", "plus"), ("-", "minus"), ("*", "star"), ("/", "slash"),
    ("%", "percent"),
    ("&", "amp"), ("|", "pipe"), ("^", "caret"), ("~", "tilde"),
    ("!", "bang"),
    ("<", "lt"), (">", "gt"),
]

INT_MIN = -(2 ** 31)
INT_MAX = 2 ** 31 - 1


@dataclass(frozen=True)
class Span:
    line: int
    col_start: int
    col_end: int
    offset: int = 0

    def to_json(self) -> dict:
        return {"line": self.line, "col": [self.col_start, self.col_end]}

    def __str__(self) -> str:
        return f"{self.line}:{self.col_start}-{self.col_end}"


@dataclass(frozen=True)
class Token:
    kind: str
    text: str
    span: Span
    value: int | None = None

    def __str__(self) -> str:
        return f"{self.kind}({self.text!r})@{self.span}"


class LexError(Exception):
    def __init__(self, message: str, span: Span):
        super().__init__(message)
        self.message = message
        self.span = span


def tokenize(src: str) -> list[Token]:
    toks: list[Token] = []
    i = 0
    line = 1
    line_start = 0
    n = len(src)

    def span_at(start: int, end: int) -> Span:
        return Span(line, start - line_start + 1, end - line_start + 1, start)

    while i < n:
        c = src[i]

        if c == "\n":
            line += 1
            i += 1
            line_start = i
            continue
        if c in " \t\r":
            i += 1
            continue

        # Comments.
        if src.startswith("//", i):
            while i < n and src[i] != "\n":
                i += 1
            continue
        if src.startswith("/*", i):
            start = i
            i += 2
            while i < n and not src.startswith("*/", i):
                if src[i] == "\n":
                    line += 1
                    i += 1
                    line_start = i
                else:
                    i += 1
            if i >= n:
                raise LexError("unterminated block comment", span_at(start, start + 2))
            i += 2
            continue

        # Integer literals: decimal or 0x-hex.
        if c.isdigit():
            start = i
            if src.startswith(("0x", "0X"), i) and i + 2 < n and _is_hex(src[i + 2]):
                i += 2
                while i < n and _is_hex(src[i]):
                    i += 1
                text = src[start:i]
                value = int(text, 16)
            else:
                while i < n and src[i].isdigit():
                    i += 1
                text = src[start:i]
                value = int(text, 10)
            sp = span_at(start, i)
            # A bare literal is non-negative, so only INT_MAX+1 can appear here
            # (as the operand of a unary minus forming INT_MIN); the parser
            # folds that case.
            if value > INT_MAX + 1:
                raise LexError(
                    f"integer literal {text} does not fit in int (max {INT_MAX})", sp)
            if i < n and (src[i].isalpha() or src[i] == "_"):
                raise LexError("malformed integer literal", span_at(start, i + 1))
            toks.append(Token("intlit", text, sp, value))
            continue

        # Identifiers and keywords.
        if c.isalpha() or c == "_":
            start = i
            while i < n and (src[i].isalnum() or src[i] == "_"):
                i += 1
            text = src[start:i]
            kind = KEYWORDS.get(text, "ident")
            toks.append(Token(kind, text, span_at(start, i)))
            continue

        # Operators and punctuation.
        for text, kind in OPERATORS:
            if src.startswith(text, i):
                toks.append(Token(kind, text, span_at(i, i + len(text))))
                i += len(text)
                break
        else:
            raise LexError(f"unexpected character {c!r}", span_at(i, i + 1))

    toks.append(Token("eof", "", span_at(n, n)))
    return toks


def _is_hex(c: str) -> bool:
    return c in "0123456789abcdefABCDEF"


def source_window(src: str, span: Span, context: int = 3) -> tuple[int, int, list[str]]:
    """Return (first_line, last_line, lines) around `span`, 1-indexed inclusive."""
    lines = src.splitlines()
    lo = max(1, span.line - context)
    hi = min(len(lines), span.line + context)
    return lo, hi, lines[lo - 1:hi]
