"""Semantic deduplication for mined rules.

A real run exposed why this is needed, not a hypothetical: across three
separate mining batches (each a fresh, memoryless model call), the model
re-derived the same identity under a different name three separate times --
`mul_by_minus_one`, `mul_neg_one_to_negate`, and `mul_by_minus_one_to_negate`
are the literal same rewrite. Nothing in `mine()`'s per-run name collision
check catches this, because the names genuinely don't collide; the
*structure* does.

This compares rules structurally rather than by name: it alpha-renames every
free register and symbolic constant to a canonical position-based name (the
first one seen in `match` becomes `%v0`, the next `%v1`, the first symbolic
constant becomes `C0`, and so on) and treats two rules as duplicates iff
their match, rewrite, and precondition are identical after that renaming.
This is intentionally syntactic, not semantic in the fullest sense: it will
not notice that `x*3` and `(x<<1)+x` compute the same thing when phrased with
different opcodes, only that two proposals are literally the same rule
modulo which names the model happened to pick. That is exactly the class of
duplication observed in practice, and is a deliberately narrower, cheaper
check than re-deriving semantic equivalence between two arbitrary rules
(which would itself require another SMT query per pair).
"""

from __future__ import annotations

from verify.ruledsl import Rule


def _canonicalize(rule: Rule):
    """A hashable signature: two rules with equal signatures are the same
    rule with different names for the same free variables/constants."""
    var_names: dict[str, str] = {}
    const_names: dict[str, str] = {}
    temp_names: dict[str, str] = {}

    def var(name: str) -> str:
        if name not in var_names:
            var_names[name] = f"v{len(var_names)}"
        return var_names[name]

    def const(name: str) -> str:
        if name not in const_names:
            const_names[name] = f"C{len(const_names)}"
        return const_names[name]

    def temp(name: str) -> str:
        if name not in temp_names:
            temp_names[name] = f"t{len(temp_names)}"
        return temp_names[name]

    def canon_reg(name: str) -> str:
        # A register is a free input the first time it's seen anywhere in
        # `match`; a name introduced by `match` or `rewrite` itself is a
        # temporary (this mirrors _infer_types' own free/defined split).
        if name in rule.free_vars:
            return var(name)
        return temp(name)

    def canon_expr(node):
        tag = node[0]
        if tag == "reg":
            return ("reg", canon_reg(node[1]))
        if tag == "csym":
            return ("csym", const(node[1]))
        if tag == "int":
            return node
        if tag == "un":
            return ("un", node[1], canon_expr(node[2]))
        if tag == "bin":
            return ("bin", node[1], canon_expr(node[2]), canon_expr(node[3]))
        if tag == "call":
            return ("call", node[1], tuple(canon_expr(a) for a in node[2]))
        if tag == "pred":
            return ("pred", node[1], tuple(canon_expr(a) for a in node[2]))
        if tag == "cmp":
            return ("cmp", node[1], canon_expr(node[2]), canon_expr(node[3]))
        if tag in ("and", "or"):
            return (tag, canon_expr(node[1]), canon_expr(node[2]))
        if tag == "not":
            return ("not", canon_expr(node[1]))
        raise AssertionError(f"unhandled node {node!r}")

    def canon_instrs(instrs):
        out = []
        for ins in instrs:
            dst = canon_reg(ins.dst)
            args = tuple(canon_expr(a) for a in ins.args)
            out.append((ins.op, ins.pred, dst, args))
        return tuple(out)

    match_sig = canon_instrs(rule.match)
    # Registers not seen in `match` (i.e. new temporaries `rewrite` defines)
    # must canonicalize the same way regardless of match/rewrite order, which
    # `canon_reg` already guarantees since it shares `temp_names` across both.
    rewrite_sig = canon_instrs(rule.rewrite)
    pre_sig = canon_expr(rule.pre) if rule.pre is not None else None
    return (match_sig, rewrite_sig, pre_sig)


def deduplicate(rules: list[Rule]) -> tuple[list[Rule], dict[str, str]]:
    """Keep the first occurrence of each structurally distinct rule.

    Returns (kept_rules, dropped_name -> kept_name), so a report can say
    exactly which proposals were re-derivations of which earlier one, rather
    than silently discarding the record of what was proposed.
    """
    seen: dict[tuple, str] = {}
    kept: list[Rule] = []
    dropped: dict[str, str] = {}
    for rule in rules:
        sig = _canonicalize(rule)
        if sig in seen:
            dropped[rule.name] = seen[sig]
        else:
            seen[sig] = rule.name
            kept.append(rule)
    return kept, dropped
