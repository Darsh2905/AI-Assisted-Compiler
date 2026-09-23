# 1. Lexer, parser, and AST

**What this is for:** turning a `.mc` source file (plain text) into a tree structure the rest of the compiler can work with.

**Key files:** `frontend/lexer.py`, `frontend/parser.py`, `frontend/ast_nodes.py`, `spec/grammar.py`

## The lexer: text → tokens

`frontend/lexer.py`'s `tokenize()` function reads the source one character at a time and groups characters into **tokens** — the smallest meaningful chunks (a keyword, a name, a number, a symbol like `{`). It's a single `while` loop with a position counter `i` that walks forward through the string:

- If it sees a digit, it keeps consuming digits (and hex digits after `0x`) until the number ends, and stores the parsed integer value alongside the text.
- If it sees a letter or underscore, it keeps consuming letters/digits/underscores, then checks whether the resulting word is a reserved keyword (`int`, `if`, `while`, ...) via a lookup in the `KEYWORDS` dictionary — if not, it's an identifier (a name).
- If it sees an operator character, it checks a list of multi-character operators first (`OPERATORS`, ordered so `<<` is checked before `<`) so it never mistakenly splits `<=` into `<` and `=`.
- Comments (`//` and `/* */`) and whitespace are consumed and thrown away — they never become tokens.

Every token that gets created is wrapped with a `Span` — the exact `(line, column_start, column_end)` it came from. This is the single most important design detail in the whole front end: **every later error message, and every entry in the "grounded context" JSON, ultimately traces back to a span assigned right here.** If the lexer didn't track this, nothing downstream could either.

## The parser: tokens → tree

`frontend/parser.py`'s `Parser` class is a **recursive-descent parser** — one method per grammar rule, and each method calls the methods for the things nested inside it. For example, `if_stmt()` calls `expr()` to parse the condition, then `block()` to parse the body.

The important property here is that this parser makes every decision by looking at just **one token ahead** (this is what "LL(1)" means). `spec/grammar.py` contains a from-scratch implementation of the classic FIRST/FOLLOW-set algorithm from compiler theory, which mechanically proves the grammar has this property — `tests/test_frontend.py::test_grammar_is_ll1_with_no_conflicts` runs it as an actual test, not just an assertion in a comment.

A few things worth knowing about how it's built:

- **Expression parsing** (`or_expr()`, `and_expr()`, `add_expr()`, `mul_expr()`, ...) is a chain of methods, one per precedence level, each calling the next-tighter-binding one. This is the standard way to encode "multiplication binds tighter than addition" without needing a separate precedence table.
- **Error recovery** (`_recover_to_top_level()`, `_recover_in_block()`) lets the parser keep going after finding one syntax error, by skipping forward to the next semicolon or brace, so that a single typo doesn't prevent every other real error in the file from being reported. This is why `broken.mc` can show 5 independent diagnostics from one compile.
- **`if`/`while`/`for` bodies must be `{ }` blocks** — you can't write `if (x) return 1;` without braces. This isn't a style preference; it's what makes the grammar LL(1) in the first place (it removes the classic "dangling else" ambiguity that a bodies-can-be-single-statements grammar has).

## The AST: what the tree actually looks like

`frontend/ast_nodes.py` defines one Python `@dataclass` per kind of syntax construct — `Binary` (a binary operator expression), `If`, `While`, `VarDecl`, and so on. Every node carries the `Span` it was parsed from (inherited from the base `Node` class), and every expression node (`Expr`) has a `ty` field that starts as `None` and gets filled in later, during semantic analysis (see [02-semantic-analysis.md](02-semantic-analysis.md)).

## See it yourself

```bash
./provitc.py tokens demo/scale.mc     # see the raw token stream
./provitc.py ast demo/scale.mc        # see the parsed tree (before type-checking runs)
python3 spec/grammar.py               # run the LL(1) conflict check directly
```
