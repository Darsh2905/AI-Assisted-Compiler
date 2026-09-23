# ProveIt-C

A compiler for a small C-like language in which an AI proposes and a
deterministic verifier decides.

Every transformation entering the optimiser is an **untrusted proposal**. It is
admitted only after an SMT solver proves it equivalent to what it replaces, over
*all* inputs, with symbolic operands and symbolic constants — so one proof
covers every instance of the pattern. Proven rules are cached in a versioned
library, and **the compiler applies that library with no model in the loop**:
no inference, no network, no sampling temperature in the build path.

> **Status: roughly 35% built.** The verification core, the front end and the
> rule machinery are real and measured. The LLM proposer (Module B) is now
> wired to a live model and has run for real — 76 proposals, 0 solver-refuted
> after surviving the cheap prefilter, at this sample size. RQ1 has a first,
> honestly-bounded answer, not a settled one — see [Module B](#module-b-a-real-llm-proposer-finally-e6)
> and [What is not built](#what-is-not-built) for exactly what that bound does
> and doesn't establish.

**Other documents in this repo:** [ARCHITECTURE.md](ARCHITECTURE.md) explains
the whole project and how its pieces fit together, with diagrams.
[docs/](docs/README.md) has one file per subsystem explaining how the code
inside it actually works, in plain language. [PRESENTING.md](PRESENTING.md)
is a step-by-step guide for actually running and demoing it.
[paper/paper.pdf](paper/paper.pdf) is the full research writeup.

```bash
pip install -r requirements.txt
python3 web/server.py                  # <- start here: paste code, see real output
./demo/demo.sh                         # 10-beat scripted walkthrough for a terminal
python3 -m pytest                      # 218 tests
python3 -m experiments.run_all         # regenerate every number below (~20 s)
./provitc.py ir bench/bitmix.mc -O     # compile and optimise
./provitc.py library                   # what the verifier has admitted
```

**`python3 web/server.py`** opens a browser at `http://localhost:8765`: paste
MiniC source (or pick a sample), click Compile, and every panel — tokens, AST,
diagnostics, IR, optimized IR with a diff, which proven rules fired, and the
full verifier table — is produced by the real `frontend/`, `ir/`, `passes/`
and `verify/` modules, the same ones the CLI and the test suite call. It is a
thin stdlib-only HTTP wrapper, not a second implementation; nothing it shows
can drift from what `./provitc.py` or `pytest` would report. It runs locally
because the backend needs Z3 and a Python process, which a static page cannot
provide.

`./demo/demo.sh` is the terminal alternative: it walks one eight-line program
from source text to optimised IR, then the proof that authorised the
optimisation, what the same checker refuses, and the measurement that
motivates the design. It pauses on Enter between sections; `--auto` runs
straight through in about four seconds and `--fast` adds short pauses for
screen recording.

---

## Why this shape

Compilers do not get to be probably right. One bad rewrite is a
*miscompilation*: the program silently computes the wrong answer. The recent
work on LLM-driven compilation converges on the same answer — put a formal
checker after the model — and then hits the same two walls.

**The checker runs out of road.** Alive2 is *bounded* translation validation.
Kwon et al. (CGO'26) report roughly 20% of TSVC loops unverifiable even after
LLM-Vectorizer's verification-friendly rewriting, and add a runtime
speculation-and-rollback layer to cover the gap. Fang et al. (CGO'26) exclude
solver timeouts from their training set outright.

**The model stays in the build.** CoV reports ~14 minutes of compile time per
loop on TSVC and ~26 minutes on real applications, dominated by model inference
and solver calls. LLM-VeriOpt runs a 3B model per function at compile time.

ProveIt-C attacks both from the language end and the caching end:

| Wall | Response |
|---|---|
| Verification is bounded because LLVM IR inherits C's undefined behaviour | Define the UB away. MiniC wraps or traps; nothing is undefined, so peephole equivalence is decidable in QF_BV and a proof is total, not bounded. |
| The model is in the compile path | Prove rules *offline*, once, with symbolic operands. The build-time artifact is a finite rule library and a pattern matcher. |

The cost is stated plainly: **MiniC is not C**, and no result here is a result
about C. See `spec/semantics.md` §7.

---

## Status

| Component | State | Evidence |
|---|---|---|
| MiniC grammar, proven LL(1) | **done** | 58 nonterminals, 109 productions, 430 table cells, **0 conflicts** (E1.1) |
| Formal semantics (wrap/trap, trap-preserving equivalence) | **done** | `spec/semantics.md`, frozen |
| Lexer, recursive-descent parser, spans | **done** | 6/6 benchmarks parse (E1.2) |
| Semantic analysis, 22 error codes, error recovery | **done** | 76/80 mutants detected (E1.3) |
| Grounded diagnostic context (Module A substrate) | **done** | 162 diagnostics, all carry span + scope + window (E1.4) |
| IR, textual form, print/parse round trip | **done** | 500 random modules, 21 opcodes, **0 failures** (E2) |
| IR generation from AST | **partial** | alloca/load/store form; **not SSA**, no phi insertion |
| Rule DSL (symbolic operands, preconditions, types) | **done** | 37 rules parse; malformed rules rejected |
| SMT encoder → QF_BV, trap-preserving | **done** | 20 proven, 17 refuted, median **1.6 ms** (E3) |
| Reference interpreter (independent 2nd implementation) | **done** | **17/17** solver counterexamples reproduced (E3.3) |
| Differential tester | **done** | 7.8M trials (E4) |
| Deterministic rule applier + cost model | **done** | byte-identical IR across runs (E5.4) |
| Constant folding, trap-safe DCE | **done** | E5 |
| Backend (llvmlite → native) | **not started** | — |
| Module A: the model call and patch verifier | **not started** | grounding layer only |
| Module B: LLM rule miner | **live, preliminary** | 76 real proposals, 0 solver-refuted-after-difftest at this sample size (E6) |
| Module C: pass ordering | **not started** | — |

---

## What is measured

All figures regenerate with `python3 -m experiments.run_all` and land in
`results/*.json`. Nothing below is typed in by hand.

### The verifier proves and rejects (E3)

37 rules: **20 proven**, **17 refuted**, zero disagreements with the verdict each
rule declares. Median solve time **1.6 ms**, max 118 ms.

Every refutation is replayed through `verify/interp.py`, an implementation of
the same specification written independently of the encoder: **17/17
reproduced**. A counterexample the interpreter could not reproduce would mean
the encoder and the spec had diverged, which is the worst failure this project
can have.

One adversarial rule is worth reading: `shl_mask_trap_erasure` masks a shift
amount to five bits, exactly as the hardware does. It is value-correct on every
input that does not trap, and is refuted **only** because it erases a trap. A
checker comparing values alone would pass it.

### Testing is budget-bound; proving is not (E4)

Of the 15 rules the solver refutes:

| Tester | 100 trials | 1,000 | 10,000 | 100,000 |
|---|---|---|---|---|
| uniform random | 73.3% | 73.3% | 73.3% | 73.3% |
| boundary-seeded | 73.3% | **93.3%** | 93.3% | 93.3% |
| **solver** | **100%** | **100%** | **100%** | **100%** |

Two things stand out. **Uniform random sampling does not improve with budget**:
a thousandfold more trials catches nothing extra, because the rules it misses
are wrong on ~1 input in 2³². Boundary seeding recovers three of them —
`x < -x` vs `x < 0` differ only at `INT_MIN` — and then also plateaus.

The fourth, a rule wrong only for the constant `0x5A5A5A5A`, **survives all
eight configurations and 7.8 million trials**. Boundary seeding is a heuristic
covering the values someone thought to enumerate; it is not a method.

Controls: 0 false alarms across 20 proven rules × 8 configurations. Per-rule
cost: the solver settles a rule in **6.7 ms**, about the price of a 1,000-trial
test run — and unlike the test run, its answer does not depend on which values
were sampled.

### Where the checker stops answering (`rulelib/hard.rules`)

Two rules multiply symbolic 32-bit values on both sides with no shared
structure. Z3 returns `unknown` on one run and `sat` on the next for the *same
query*. They are quarantined and only the safety half is asserted: an unsettled
query must never be reported `PROVEN`. A timeout costs coverage, never
soundness.

### Proven is not the same as profitable (E5)

The first run of the applier grew the benchmark suite by ~60%.
`add_split_or_and` — `x + y == (x|y) + (x&y)` — is perfectly correct and turns
one instruction into three. It fired **375 times**.

A proof says a rewrite preserves meaning. It says nothing about whether the
rewrite is an improvement, and the two decisions need separate machinery. The
applier now consults a static cost model and fires a rule only when it
*strictly* reduces cost: **12 of 20** proven rules qualify.

The resulting reduction is **1.5%** across the suite, and the honest reason is
that **only 2 of 12 applicable rules ever match six programs**. That is a
statement about the corpus, not the method, and it is the strongest argument
for the corpus work that comes next.

### Module B: a real LLM proposer, finally (E6)

`llm/` wires in a live proposer — `openai/gpt-oss-120b` via Groq's API,
temperature 0, prompt teaching the exact rule grammar — and runs every
proposal through the same pipeline every hand-written rule goes through:
parse → differential test → SMT. This is the piece the rest of the project
has been waiting for; RQ1 has a first, small, honestly-bounded answer.

**Three batches, one model, iterating on the prompt between each:**

| | Batch 1 | Batch 2 | Batch 3 |
|---|---|---|---|
| malformed (didn't parse) | 21/25 (84%) | 5/25 (20%) | 1/25 (3.8%) |
| refuted by differential test | 0 | 2 | 1 |
| survived to the solver | 4 | 18 | 24 |
| **solver-refuted (of those)** | 0 | 0 | 0 |
| proven & cost-reducing | 2 | 7 | 11 |

**Batch 1's 84% malformed rate was a real grammar gap, not a bad model.**
Every single one of the 21 failures was the model writing `%r = %x` — a bare
passthrough — for a correct, well-known identity like `x + 0 == x`. The rule
DSL has no passthrough instruction; our own hand-written rules dodge this by
writing `add %x, 0` instead, a convention we'd never told the model. One
paragraph and one worked example dropped the rate to 20%.

**Batch 2 found a second, equally clean gap.** Five proposals called `not`
(our grammar's *boolean* negation) on a 32-bit register, meaning bitwise
complement — which the grammar expresses as `xor %x, -1`, not `not`. Another
paragraph, another worked example: 3.8%.

**Every bug the cheap prefilter caught was a bug we already knew about.**
Across all three batches, differential testing refuted exactly 3 proposals —
and all three independently reproduced a bug class already sitting in
`adversarial.rules`: the classic `sdiv`/`ashr` sign-truncation error (twice)
and the `srem`-as-bitmask error that's wrong for negative dividends (once).
Nobody prompted for these. That's real, if modest, evidence the adversarial
rules used throughout this project aren't contrived.

**The headline number: of the 46 proposals that survived the cheap prefilter,
zero were refuted by the solver** — at this sample size, with this model and
this prompt. We report that as a *bound*, not a rate: the prompt explicitly
asks for well-known optimization categories, which is a reasonable way to
bootstrap a library and a biased way to go looking for the near-miss rules
`stealth.rules` was built to resemble. A larger sample, a different model, or
a prompt asking for riskier rewrites might find a nonzero count.

**Deduplication mattered.** 20 rules were accepted; only **14 were
structurally distinct**. The model re-derived the same identity — same
match, same rewrite, different variable names — under three different names
across three separate, memoryless calls (`mul_by_minus_one` /
`mul_neg_one_to_negate` / `mul_by_minus_one_to_negate` are the literal same
rule). `llm/dedupe.py` catches this by alpha-renaming every free variable and
constant to a canonical position-based name and comparing structurally.

Applying the 14 distinct mined rules alongside the hand-written library left
E5's benchmark-suite reduction **unchanged at 1.5%** — the mined rules either
overlap coverage the hand-written library already had, or target patterns
this six-program corpus doesn't contain. Same conclusion as E5, reinforced
from a different angle: the corpus is the bottleneck, not the rule count.

**A bug we caught in our own new code while building this:** an early version
of the benchmark comparison above applied *every* hand-written candidate rule
— including the ones in `adversarial.rules` that are refuted on purpose —
without filtering to only what the solver had actually proven. It produced a
plausible-looking but wrong number, caught only because it didn't match E5's
already-published baseline. Fixed, tested (`tests/test_exp06_benchmark_impact.py`),
and reported here rather than quietly corrected — exactly the project's own
stated practice.

Every previously-published E3/E4/E5 number was re-verified to reproduce
*exactly* after Module B was added — `rulelib/mined.rules` existing on disk
never silently moves a number this project already reported (see
`experiments/_common.py`'s `hand_written_rule_files()`).

**Setup:** copy `.env.example` to `.env` and fill in `GROQ_API_KEY` (get one
free at [console.groq.com](https://console.groq.com/keys)), then
`python3 -m experiments.exp06_llm_miner 25`. This is the one experiment that
touches the network and is therefore **not** part of `./run_all.sh` — raw
responses are cached to `results/llm_cache/` by prompt hash so a re-run
against an unchanged prompt costs no API call.

### Front end (E1)

80 mutants from six operators: **95% detected**, 5% silent. 55% of detected
faults are reported on the injected line — the misses are concentrated in
`drop_return`, where the correct diagnostic points at the function signature
rather than the deleted line, so the localisation metric penalises a correct
answer.

Of 162 diagnostics, 75 carry `expected`/`found`; the rest are classes
(undefined name, missing return) where those fields are not meaningful. **All**
carry a span, the in-scope symbol list and a source window. No model is
involved in producing any of it.

---

## What is not built

- **RQ1 has a first answer, not a settled one.** Module B (E6) ran a real
  model through the real pipeline — 76 proposals, 0 solver-refutations after
  surviving the cheap prefilter, at one model and one prompt design. That's a
  bound at this sample size, not a population rate; see E6 above for exactly
  what would need to change to turn it into one (more proposals, more models,
  prompts that ask for riskier rewrites instead of textbook identities).
- **Module A's model half still doesn't exist.** The grounded-context
  substrate is real (E1.4); the actual model call and recompile-check loop
  that would turn a diagnostic into a verified patch is not built.
- **The IR is not in SSA form.** `ir/irgen.py` lowers locals to
  `alloca`/`load`/`store`; mem2reg and phi insertion are not written. `phi` is
  in the representation and the round-trip tests, not in generated code.
- **No backend.** Nothing executes natively; `llvmlite` is pinned but unused.
- **The cost model is ordinal placeholders**, not measured latencies. No
  performance claim is made and none should be read in.
- **Matching is syntactic and block-local.** No reassociation, no canonical
  form. Reported match rates are a floor.
- **Preconditions constrain constants only**, so rules needing a fact about a
  *value* (`x % 2^k == x & (2^k-1)` for non-negative `x`) cannot be expressed.

---

## Layout

```
spec/        grammar (machine-readable + LL(1) analyser), formal semantics
frontend/    lexer, parser, AST, semantic analysis, structured diagnostics
ir/          IR types, printer, parser, IR generation
passes/      constant folding, trap-safe DCE, the rule applier + cost model
verify/      rule DSL, SMT encoder, reference interpreter, differential tester
llm/         Module B: Groq client, DSL-teaching prompt, miner, deduplicator
rulelib/     textbook (proven) · adversarial (refuted) · stealth · hard
             · mined (Module B, live, kept separate — see E6)
bench/       MiniC benchmark programs
demo/        scripted terminal walkthrough (demo.sh) + its two sample programs
experiments/ one script per result; run_all regenerates everything
             (except exp06_llm_miner.py, the one that touches the network)
tests/       218 pytest assertions with explicit thresholds
web/         server.py (local playground, real backend) + app.html (its UI)
             + index.html (static status page, published at
             https://claude.ai/artifact/JtmG9ve444JczMKztEkDR6)
paper/       IEEE draft (paper.tex + paper.pdf, 10 pages, builds clean)
.env.example copy to .env and fill in GROQ_API_KEY to run Module B
```

## Reproducing

```bash
python3 -m pytest -q                   # every assertion has a threshold
python3 -m experiments.run_all         # rewrites results/*.json
./run_all.sh                           # both, in one command
```

`paper/paper.tex` needs a TeX distribution (not installed here); run
`pdflatex` three times so cross-references settle.
