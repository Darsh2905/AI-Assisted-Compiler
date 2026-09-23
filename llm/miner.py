"""Module B: propose candidate rules, verify every one, measure the gap.

This is the piece the paper's abstract says is missing, and the reason RQ1
("how often is a test-passing rewrite actually wrong?") is unanswered. The
loop is exactly the trust boundary from Fig. 1: a proposal is untrusted text
until it (a) parses into the rule DSL, (b) survives cheap differential
testing, and (c) is proven by the SMT encoder. Nothing here applies a rule to
real code -- that stays in `passes/peephole.py`, and only PROVEN,
cost-reducing rules are ever eligible to reach it.

`mine()` takes a `propose_fn: Callable[[int], str]` rather than a GroqClient
directly, so the whole pipeline -- extraction, renaming, parsing, differential
testing, SMT verification, aggregation -- is testable with a scripted fake
proposer and no network call. `experiments/exp06_llm_miner.py` is the only
place that wires in a real `GroqClient`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

from passes.peephole import is_profitable, side_cost
from verify.difftest import run_difftest
from verify.ruledsl import Rule, RuleSyntaxError, RuleTypeError, parse_rules
from verify.smt_encode import PROVEN, verify_rule

_RULE_HEAD = re.compile(r"\brule\s+([A-Za-z_]\w*)\s*\{")


def extract_rule_blocks(text: str) -> list[str]:
    """Pull out each `rule NAME { ... }` block via brace counting.

    Deliberately not a call to `parse_rules` on the whole response: a model
    that wraps its answer in prose or a markdown fence would make the whole
    batch fail to parse, when in fact most of the individual rules might be
    fine. Isolating blocks first means a malformed rule costs one data point,
    not the whole batch -- and "the model added prose it was told not to" is
    itself worth recording, which `mine()` does via `extra_text`.
    """
    blocks: list[str] = []
    for m in _RULE_HEAD.finditer(text):
        start = m.start()
        depth = 0
        i = m.end() - 1          # position of the opening '{'
        for i in range(m.end() - 1, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    break
        else:
            continue              # unbalanced braces: truncated response
        blocks.append(text[start:i + 1])
    return blocks


def _dedupe_name(block: str, taken: set[str]) -> tuple[str, str]:
    """Rename a proposal's rule if its name collides with one already seen.

    The model is told not to repeat names, but nothing stops it, and a
    silent collision would make `parse_rules` attribute a verdict to the
    wrong rule. Returns (possibly renamed block, final name).
    """
    m = _RULE_HEAD.match(block)
    original = m.group(1)
    name = original
    n = 2
    while name in taken:
        name = f"{original}_{n}"
        n += 1
    taken.add(name)
    if name != original:
        block = block[:m.start(1)] + name + block[m.end(1):]
    return block, name


@dataclass
class Proposal:
    """One candidate rule's full trip through the trust boundary."""
    raw_name: str
    rule_name: str
    source: str
    parse_ok: bool = False
    parse_error: str | None = None
    difftest_verdict: str | None = None       # "SURVIVED" | "REFUTED" | None
    difftest_counterexample: dict = field(default_factory=dict)
    smt_status: str | None = None
    smt_counterexample: dict = field(default_factory=dict)
    smt_detail: str = ""
    solve_ms: float = 0.0
    match_cost: int | None = None
    rewrite_cost: int | None = None
    profitable: bool | None = None
    accepted: bool = False                     # PROVEN, profitable, ready to save
    rule: Rule | None = field(default=None, repr=False, compare=False)

    def to_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if k != "rule"}
        return d


@dataclass
class MiningRun:
    requested: int
    raw_response: str
    extra_text_present: bool          # model wrote something outside rule blocks
    proposals: list[Proposal] = field(default_factory=list)

    # ------------------------------------------------------------- counts --
    @property
    def total(self) -> int:
        return len(self.proposals)

    def count(self, pred: Callable[[Proposal], bool]) -> int:
        return sum(1 for p in self.proposals if pred(p))

    @property
    def malformed(self) -> int:
        return self.count(lambda p: not p.parse_ok)

    @property
    def parsed(self) -> int:
        return self.count(lambda p: p.parse_ok)

    @property
    def difftest_refuted(self) -> int:
        return self.count(lambda p: p.difftest_verdict == "REFUTED")

    @property
    def survived_difftest(self) -> int:
        return self.count(lambda p: p.difftest_verdict == "SURVIVED")

    @property
    def smt_refuted_after_surviving_difftest(self) -> int:
        """The headline number: passed the cheap check, failed the real one."""
        return self.count(
            lambda p: p.difftest_verdict == "SURVIVED" and p.smt_status == "REFUTED")

    @property
    def proven(self) -> int:
        return self.count(lambda p: p.smt_status == PROVEN)

    @property
    def accepted(self) -> int:
        return self.count(lambda p: p.accepted)

    def summary(self) -> dict:
        return {
            "requested": self.requested,
            "extracted": self.total,
            "extra_text_present": self.extra_text_present,
            "malformed": self.malformed,
            "parsed": self.parsed,
            "difftest_refuted": self.difftest_refuted,
            "survived_difftest": self.survived_difftest,
            "smt_refuted_after_surviving_difftest": self.smt_refuted_after_surviving_difftest,
            "proven": self.proven,
            "accepted": self.accepted,
        }


def mine(propose_fn: Callable[[int], str], n: int,
         existing_rule_names: set[str] | None = None,
         difftest_trials: int = 5_000, difftest_seed: int = 0xC0FFEE,
         smt_timeout_ms: int = 10_000) -> MiningRun:
    """Request `n` rules, run every one through the full trust boundary.

    Order matters and mirrors Fig. 1 of the paper: parse, then the cheap
    differential-testing prefilter, then the SMT solver -- so a malformed or
    cheaply-refutable proposal never spends solver time.
    """
    raw = propose_fn(n)
    blocks = extract_rule_blocks(raw)
    stripped = re.sub(r"\s+", "", raw)
    stripped_blocks = re.sub(r"\s+", "", "".join(blocks))
    extra_text = len(stripped) > len(stripped_blocks)

    run = MiningRun(requested=n, raw_response=raw, extra_text_present=extra_text)
    taken = set(existing_rule_names or ())

    for block in blocks:
        m = _RULE_HEAD.match(block)
        raw_name = m.group(1) if m else "?"
        block, final_name = _dedupe_name(block, taken)
        p = Proposal(raw_name=raw_name, rule_name=final_name, source=block)

        try:
            parsed = parse_rules(block)
        except (RuleSyntaxError, RuleTypeError) as e:
            p.parse_error = str(e)
            run.proposals.append(p)
            continue
        if len(parsed) != 1:
            p.parse_error = f"expected 1 rule in this block, got {len(parsed)}"
            run.proposals.append(p)
            continue

        rule = parsed[0]
        rule.origin = "llm_mined"
        p.parse_ok = True
        p.rule = rule
        p.match_cost = side_cost(rule.match)
        p.rewrite_cost = side_cost(rule.rewrite)

        dt = run_difftest(rule, trials=difftest_trials, seed=difftest_seed)
        if dt.found:
            p.difftest_verdict = "REFUTED"
            p.difftest_counterexample = dt.counterexamples[0]["env"] if dt.counterexamples else {}
            run.proposals.append(p)
            continue
        p.difftest_verdict = "SURVIVED"

        v = verify_rule(rule, timeout_ms=smt_timeout_ms)
        p.smt_status = v.status
        p.smt_counterexample = v.counterexample
        p.smt_detail = v.detail
        p.solve_ms = v.solve_ms

        if v.status == PROVEN:
            p.profitable = is_profitable(rule)
            p.accepted = p.profitable

        run.proposals.append(p)

    return run
