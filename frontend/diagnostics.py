"""Structured diagnostics.

A diagnostic here is not a string. It is a record with a stable code, a span,
and — for the type errors that Module A targets — the compiler state that made
the error decidable: what was expected, what was found, and what identifiers
were in scope at that point.

That distinction is the whole of Module A's hypothesis (RQ4): a model handed
`expected int, found bool, in scope {count: int, done: bool}` should propose
better patches than one handed `error: type mismatch on line 14`. The grounding
layer is implemented and measurable on its own; the model call is not yet
wired up, and `to_context()` is the boundary between the two.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from frontend.lexer import Span, source_window

ERROR_CODES = {
    # Lexical and syntactic
    "E0001": "unexpected token",
    "E0002": "unexpected character",
    "E0003": "unterminated block comment",
    "E0004": "malformed integer literal",
    # Names
    "E0101": "undefined variable",
    "E0102": "variable already declared in this scope",
    "E0103": "undefined function",
    "E0104": "function already defined",
    # Types
    "E0201": "type mismatch in assignment",
    "E0202": "operand type mismatch",
    "E0203": "condition must be bool",
    "E0204": "integer literal out of range",
    # Calls
    "E0301": "wrong number of arguments",
    "E0302": "argument type mismatch",
    # Returns
    "E0401": "missing return on some path",
    "E0402": "return type mismatch",
    "E0403": "cannot return a value from a void function",
    # Arrays
    "E0501": "array index must be int",
    "E0502": "subscript applied to a non-array",
    "E0503": "array size must be a positive integer literal",
    "E0504": "array cannot be used as a value",
    # Assignability
    "E0601": "target of assignment is not an lvalue",
    "E0602": "callee is not a function",
    "E0701": "variable cannot have type void",
}


@dataclass
class Diagnostic:
    code: str
    span: Span
    message: str
    expected: str | None = None
    found: str | None = None
    in_scope: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def __post_init__(self):
        if self.code not in ERROR_CODES:
            raise ValueError(f"unknown diagnostic code {self.code}")

    @property
    def title(self) -> str:
        return ERROR_CODES[self.code]

    def render(self, src: str | None = None, filename: str = "<input>") -> str:
        head = (f"{filename}:{self.span.line}:{self.span.col_start}: "
                f"error[{self.code}]: {self.message}")
        if src is None:
            return head
        lines = src.splitlines()
        if not (1 <= self.span.line <= len(lines)):
            return head
        text = lines[self.span.line - 1]
        gutter = f"{self.span.line:>4} | "
        width = max(1, self.span.col_end - self.span.col_start)
        caret = " " * len(gutter) + " " * (self.span.col_start - 1) + "^" * width
        out = [head, gutter + text, caret]
        out += [f"       note: {n}" for n in self.notes]
        return "\n".join(out)

    def to_context(self, src: str, context_lines: int = 3) -> dict:
        """The grounded context blob that Module A will hand to a model.

        Deliberately a pure function of compiler state — no prose, no model
        output, nothing that could vary between runs.
        """
        lo, hi, window = source_window(src, self.span, context_lines)
        blob = {
            "error_code": f"{self.code}_{self.title.upper().replace(' ', '_')}",
            "span": self.span.to_json(),
            "message": self.message,
            "source_window": {"first_line": lo, "last_line": hi,
                              "text": "\n".join(window)},
        }
        if self.expected is not None:
            blob["expected"] = self.expected
        if self.found is not None:
            blob["found"] = self.found
        if self.in_scope:
            blob["in_scope"] = self.in_scope
        if self.notes:
            blob["notes"] = self.notes
        return blob

    def to_json(self, src: str) -> str:
        return json.dumps(self.to_context(src), indent=2, sort_keys=True)


class CompileError(Exception):
    """Raised when compilation cannot continue; carries the diagnostics so far."""

    def __init__(self, diagnostics: list[Diagnostic]):
        super().__init__(f"{len(diagnostics)} diagnostic(s)")
        self.diagnostics = diagnostics
