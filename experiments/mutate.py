"""Deterministic mutation of correct MiniC programs into faulty ones.

Module A's experiment (RQ4) needs a corpus of programs that are broken in the
ways students actually break them, with a known ground truth about where the
fault is. Mutating known-good programs gives exactly that, and it is
reproducible in a way that scraping real student submissions is not.

Each operator mutates the *source text*, because the faults of interest are
textual: a swapped type keyword, a deleted declaration, a mistyped name. The
injected span is recorded so a future patch-acceptance experiment can ask
whether a proposed fix touches the right line.

A mutant that still compiles cleanly is reported as `silent` rather than
discarded: a mutation operator that usually produces silent mutants is a bad
operator, and hiding that would overstate the corpus's difficulty.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass


@dataclass
class Mutant:
    program: str
    operator: str
    source: str
    injected_line: int
    description: str


def _lines_with(src: str, pattern: str) -> list[tuple[int, str]]:
    return [(i, ln) for i, ln in enumerate(src.splitlines())
            if re.search(pattern, ln)]


def _rebuild(src: str, index: int, replacement: str | None) -> str:
    lines = src.splitlines()
    if replacement is None:
        del lines[index]
    else:
        lines[index] = replacement
    return "\n".join(lines) + "\n"


def op_swap_type(src: str, rng: random.Random):
    """`int x = ...` becomes `bool x = ...` — the type-mismatch case."""
    cands = _lines_with(src, r"^\s*int\s+\w+\s*=")
    if not cands:
        return None
    i, line = rng.choice(cands)
    return (_rebuild(src, i, re.sub(r"\bint\b", "bool", line, count=1)),
            i + 1, "declared type int changed to bool")


def op_delete_decl(src: str, rng: random.Random):
    """Delete a local declaration, leaving its later uses undefined."""
    cands = [(i, ln) for i, ln in _lines_with(src, r"^\s*(int|bool)\s+\w+\s*=")
             if "[" not in ln]
    if not cands:
        return None
    i, line = rng.choice(cands)
    name = re.search(r"\b(?:int|bool)\s+(\w+)", line).group(1)
    if len(_lines_with(src, rf"\b{name}\b")) < 2:
        return None                       # unused: deleting it is silent
    return _rebuild(src, i, None), i + 1, f"declaration of '{name}' deleted"


def op_typo_identifier(src: str, rng: random.Random):
    """Transpose two characters of an identifier use — the misspelling case."""
    cands = _lines_with(src, r"return\s+[A-Za-z_]\w{3,}")
    if not cands:
        return None
    i, line = rng.choice(cands)
    m = re.search(r"return\s+([A-Za-z_]\w{3,})", line)
    name = m.group(1)
    if name in {"true", "false"}:
        return None
    k = rng.randrange(len(name) - 1)
    typo = name[:k] + name[k + 1] + name[k] + name[k + 2:]
    if typo == name:
        return None
    return (_rebuild(src, i, line.replace(name, typo, 1)), i + 1,
            f"identifier '{name}' misspelled as '{typo}'")


def op_drop_return(src: str, rng: random.Random):
    """Delete a `return` that a path depends on — the missing-return case."""
    cands = [(i, ln) for i, ln in _lines_with(src, r"^\s*return\b")
             if ln.strip().endswith(";")]
    if len(cands) < 2:
        return None
    i, _ = rng.choice(cands)
    return _rebuild(src, i, None), i + 1, "a return statement was deleted"


def op_drop_argument(src: str, rng: random.Random):
    """Drop one argument at a call site — the arity case."""
    cands = _lines_with(src, r"\w+\([^()]*,[^()]*\)")
    if not cands:
        return None
    i, line = rng.choice(cands)
    m = re.search(r"(\w+)\(([^()]*,[^()]*)\)", line)
    args = [a.strip() for a in m.group(2).split(",")]
    dropped = args.pop(rng.randrange(len(args)))
    mutated = line[:m.start(2)] + ", ".join(args) + line[m.end(2):]
    return (_rebuild(src, i, mutated), i + 1,
            f"argument '{dropped}' removed from a call to '{m.group(1)}'")


def op_int_condition(src: str, rng: random.Random):
    """Replace `x < y` in a condition with bare `x` — the int-as-bool case."""
    cands = _lines_with(src, r"(if|while)\s*\(\s*\w+\s*[<>]=?\s*[\w]+\s*\)")
    if not cands:
        return None
    i, line = rng.choice(cands)
    mutated = re.sub(r"(if|while)\s*\(\s*(\w+)\s*[<>]=?\s*\w+\s*\)",
                     r"\1 (\2)", line, count=1)
    return (_rebuild(src, i, mutated), i + 1,
            "comparison in a condition replaced by a bare int")


OPERATORS = {
    "swap_type": op_swap_type,
    "delete_decl": op_delete_decl,
    "typo_identifier": op_typo_identifier,
    "drop_return": op_drop_return,
    "drop_argument": op_drop_argument,
    "int_condition": op_int_condition,
}


def generate(program: str, src: str, per_operator: int = 3,
             seed: int = 20260916) -> list[Mutant]:
    rng = random.Random(f"{seed}:{program}")
    out: list[Mutant] = []
    seen: set[str] = set()
    for name, op in OPERATORS.items():
        produced = 0
        for _ in range(per_operator * 8):
            if produced >= per_operator:
                break
            try:
                result = op(src, rng)
            except (AttributeError, ValueError, IndexError):
                result = None
            if result is None:
                continue
            mutated, line, description = result
            if mutated == src or mutated in seen:
                continue
            seen.add(mutated)
            out.append(Mutant(program, name, mutated, line, description))
            produced += 1
    return out
