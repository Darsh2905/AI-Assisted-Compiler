# 9. Experiments and tests: how every number gets produced

**What this is for:** the project's core discipline — every quantitative claim in the paper and README comes from a script that anyone can re-run, and every piece of logic has a test that asserts a specific, checkable outcome rather than just "the script didn't crash."

**Key files:** `experiments/_common.py`, `experiments/exp01`–`exp06`, `tests/*.py`

## The experiments: `results/*.json` in, prose out

`experiments/_common.py` has the shared plumbing every experiment script uses: `bench_programs()` finds the sample `.mc` files, `rule_files()` finds every `.rules` file, `save()` writes a result to `results/<name>.json`. One function is worth understanding specifically:

`hand_written_rule_files()` returns every rule file **except** `rulelib/mined.rules`. This exists for a concrete reason: once Module B started writing real, proven rules into `mined.rules`, any experiment that just globbed "every `.rules` file" would have its numbers silently shift every time a new mining batch ran — including numbers already published in the paper. Experiments 3, 4, and 5 (verification, differential-testing-vs-SMT, and rule-application) all use this filtered list specifically so that `rulelib/mined.rules` existing on disk can never quietly change a number that was already reported. Module B's own experiment (E6) reports the with-mined-rules comparison explicitly and separately, never by mutating an existing number.

Each experiment script (`exp01_frontend.py` through `exp06_llm_miner.py`) follows the same shape: run some part of the compiler, print a human-readable table via `C.table()`, and save the same data as JSON. `experiments/run_all.py` runs every experiment except `exp06` (the one that needs a live API key and network access) and prints a final headline-numbers summary — this is what `./run_all.sh` calls.

## The test suite: assertions, not vibes

`tests/` mirrors the source layout roughly one file per subsystem. The thing worth understanding about how these tests are written, more than any individual test, is a rule stated directly in the project's own instructions and enforced throughout: **a regression check that only verifies "the script didn't crash" is the same class of mistake this entire project exists to argue against.** Every test in this suite asserts a specific number, a specific verdict, or a specific exception — never just "ran without error."

A few tests worth knowing about because they demonstrate this project auditing its own work:

- `tests/test_verify.py::test_stealth_rules_evade_uniform_testing_but_not_the_solver` — asserts the exact phenomenon this project is built around: certain rules survive 10,000 rounds of plain random testing while the solver still catches them every time.
- `tests/test_dedupe.py::test_the_actual_accumulated_mined_rules_file_deduplicates_correctly` — runs the deduplicator described in [08-module-b-llm.md](08-module-b-llm.md) against the real, live output of a real mining run, not a synthetic example.
- `tests/test_exp06_benchmark_impact.py` — a regression test for a real bug: an early version of the Module B benchmark comparison applied *unverified* rules to real code (including deliberately-wrong ones from `adversarial.rules`) before filtering to only what the solver had actually proven. It produced a plausible-looking but wrong number, caught only because it didn't match an already-published baseline. This test exists specifically so that exact mistake can never silently happen again.

## See it yourself

```bash
python3 -m pytest -v                   # every test, with names
python3 -m experiments.run_all         # regenerate every number (except Module B's)
./run_all.sh                            # both, plus the grammar check; exits non-zero on any regression
```
