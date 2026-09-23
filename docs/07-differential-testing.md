# 7. Differential testing: the cheap check before the expensive one

**What this is for:** a fast, approximate filter that runs *before* the SMT solver, so that obviously-wrong rules get rejected in microseconds instead of costing a full formal proof attempt. Also, in its own right, the experiment that demonstrates exactly why "tested a lot" is not the same claim as "proven."

**Key file:** `verify/difftest.py`

## The idea

`run_difftest()` picks a batch of concrete values for a rule's free registers and symbolic constants, plugs them into both the `match` side and the `rewrite` side using `verify/interp.py`'s evaluator (the same reference interpreter [06-smt-verification.md](06-smt-verification.md) describes), and checks whether the two sides agree — same value, same trap behavior — for every sampled input. The moment one disagreement is found, the rule is rejected, and the specific inputs that broke it are recorded as a counterexample.

## Why sampling alone isn't the answer

`POOL` is a curated set of "interesting" 32-bit values — 0, 1, -1, `INT_MIN`, `INT_MAX`, every power of two, common bitmasks, and so on — because these are exactly the values that tend to expose bugs (overflow, sign-truncation, boundary conditions) that uniformly-random sampling would almost never stumble onto by chance. `_sample()` mixes pure random values with draws from this pool, controlled by a `boundary_rate` parameter (default 0.5 — half the time it draws from the interesting-value pool instead of a uniform random 32-bit number).

The project's own measurements (see `rulelib/stealth.rules` and `experiments/exp04_difftest_vs_smt.py`) show exactly where this still falls short: rules wrong only at a boundary value like `INT_MIN` get caught reliably by boundary-seeded sampling, but a rule that's wrong only for one arbitrary, unremarkable constant (not `0`, not a power of two, nothing "interesting") can survive *millions* of trials undetected, because no reasonable sampling strategy has any particular reason to draw that exact number. The SMT solver in [06-smt-verification.md](06-smt-verification.md) catches it instantly, because it isn't sampling at all — it's reasoning about every possible input at once.

## Acceptance rate: a quiet failure mode worth watching for

A rule's precondition might be true for only a small fraction of randomly sampled inputs (for example, `is_pow2(C)` is true for only 32 out of roughly 4 billion possible 32-bit values of `C`). If most trials get thrown away for failing the precondition, a test can report "0 counterexamples found" while having barely tested anything at all — which looks identical to a thorough, clean result unless you also look at how many trials were actually *accepted*. `DiffResult.acceptance` tracks and reports this ratio explicitly for exactly this reason.

## See it yourself

```bash
python3 -c "
from verify.ruledsl import load_rules
from verify.difftest import run_difftest
r = load_rules('rulelib/stealth.rules')[-1]   # the hardest-to-catch rule in the project
print(run_difftest(r, trials=100_000, boundary_rate=0.5))
"
```

`experiments/exp04_difftest_vs_smt.py` runs this comparison systematically, across every refuted rule in the project, at multiple trial budgets and sampling strategies — it's the single most detailed report on exactly where testing succeeds and fails.
