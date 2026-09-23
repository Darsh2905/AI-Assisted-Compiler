"""Differential testing of a rewrite rule on concrete inputs.

This is the cheap prefilter: most wrong proposals die here in microseconds, so
the solver is only asked about candidates that already survived a beating. It
is *not* a correctness argument. Testing a finite slice of a 2^32 (or 2^64)
input space cannot establish equivalence, and the gap between "passed the
tests" and "is equivalent" is precisely what this project sets out to measure
(RQ1). Taneja et al. and Fang et al. both report I/O-verified transformations
that formal verification later refutes.

Two sampling knobs exist so that gap can be characterised rather than asserted:

* `boundary_rate` — how often an operand is drawn from the interesting-value
  pool instead of uniformly at random. Boundary seeding is what catches the
  classic INT_MIN/INT_MAX faults, and setting it to 0 shows how much of a
  tester's power comes from the values someone thought to enumerate.
* `trials` — sample budget.

`acceptance` is reported for a reason: if a rule's precondition is rarely
satisfied by the sampler, almost every trial is thrown away and the test is
near-vacuous while still printing "0 counterexamples".
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field

from verify.interp import compare_sides, check_precondition
from verify.ruledsl import Rule

WIDTH = 32
MASK = (1 << WIDTH) - 1
INT_MIN = -(1 << (WIDTH - 1))
INT_MAX = (1 << (WIDTH - 1)) - 1


def _pool() -> list[int]:
    """Interesting 32-bit values, as unsigned."""
    values = {0, 1, 2, 3, 4, 5, 6, 7, 8, 10, 16, 31, 32, 33, 63, 64, 100, 255,
              256, 1000, 65535, 65536}
    values |= {1 << i for i in range(WIDTH)}          # powers of two
    values |= {(1 << i) - 1 for i in range(1, WIDTH)}  # low-bit masks
    values |= {INT_MAX & MASK, (INT_MAX - 1) & MASK}
    values |= {INT_MIN & MASK, (INT_MIN + 1) & MASK}
    values |= {(-v) & MASK for v in list(values)}      # negations
    values |= {0x55555555, 0xAAAAAAAA, 0x0F0F0F0F, 0xF0F0F0F0, 0xDEADBEEF}
    return sorted(values)


POOL = _pool()


@dataclass
class DiffResult:
    rule: str
    trials: int
    accepted: int
    rejected: int
    counterexamples: list[dict] = field(default_factory=list)
    first_at: int | None = None
    elapsed_ms: float = 0.0
    boundary_rate: float = 0.5

    @property
    def found(self) -> bool:
        return bool(self.counterexamples)

    @property
    def acceptance(self) -> float:
        total = self.accepted + self.rejected
        return self.accepted / total if total else 0.0

    @property
    def verdict(self) -> str:
        return "REFUTED" if self.found else "SURVIVED"

    def __str__(self) -> str:
        head = (f"{self.rule}: {self.verdict} after {self.accepted} accepted "
                f"trial(s) [acceptance {self.acceptance:.2%}]")
        if self.found:
            cex = self.counterexamples[0]
            env = ", ".join(f"{k}={_s(v)}" for k, v in sorted(cex["env"].items()))
            head += f"\n    first at trial {self.first_at}: {env} -> {cex['why']}"
        return head


def _s(v: int) -> int:
    v &= MASK
    return v - (1 << WIDTH) if v & (1 << (WIDTH - 1)) else v


def _sample(rng: random.Random, boundary_rate: float, width: str) -> int:
    if width == "i1":
        return rng.getrandbits(1)
    if rng.random() < boundary_rate:
        return rng.choice(POOL)
    return rng.getrandbits(WIDTH)


def run_difftest(rule: Rule, trials: int = 10_000, seed: int = 0xC0FFEE,
                 boundary_rate: float = 0.5,
                 const_boundary_rate: float = 0.9,
                 max_counterexamples: int = 3) -> DiffResult:
    rng = random.Random(seed)
    start = time.perf_counter()
    accepted = rejected = 0
    found: list[dict] = []
    first_at: int | None = None

    for trial in range(1, trials + 1):
        env: dict[str, int] = {}
        for name in rule.free_vars:
            env[name] = _sample(rng, boundary_rate, rule.types.get(name, "i32"))
        for name in rule.const_syms:
            env[name] = _sample(rng, const_boundary_rate, "i32")

        try:
            if not check_precondition(rule, env):
                rejected += 1
                continue
        except Exception:                          # noqa: BLE001
            rejected += 1
            continue

        accepted += 1
        agree, why = compare_sides(rule, env)
        if not agree:
            if first_at is None:
                first_at = trial
            if len(found) < max_counterexamples:
                found.append({"env": dict(env), "why": why, "trial": trial})

    return DiffResult(rule.name, trials, accepted, rejected, found, first_at,
                      (time.perf_counter() - start) * 1000.0, boundary_rate)


def replay(rule: Rule, assignment: dict[str, int]) -> tuple[bool, str]:
    """Evaluate one concrete assignment — used to confirm SMT counterexamples.

    A counterexample the solver reports but the reference interpreter cannot
    reproduce means the encoding and the specification have diverged.
    """
    env = {k: v & MASK for k, v in assignment.items()}
    for name in rule.free_vars + rule.const_syms:
        env.setdefault(name, 0)
    return compare_sides(rule, env)
