# 8. Module B: the live LLM proposer

**What this is for:** the actual "AI proposes" half of "AI proposes, verifier decides." Everything before this doc explains a pipeline that could check rules from anywhere; this is where a real language model gets plugged into that pipeline.

**Key files:** `llm/env.py`, `llm/groq_client.py`, `llm/prompts.py`, `llm/miner.py`, `llm/dedupe.py`

## The pieces, in the order data flows through them

### 1. `llm/env.py` — reading the API key safely

A minimal `.env` file parser (about a dozen lines). It exists because a terminal `export` only lives in that terminal's own process and never reaches a separately-launched script — a file is the only thing that reliably survives between processes. `require()` reads the key and raises a clear error naming exactly what's missing if it isn't set; nothing here ever logs or prints the key itself.

### 2. `llm/groq_client.py` — talking to the model

A small wrapper around Groq's chat-completions API, built on the standard library's `urllib` rather than a new dependency. Two non-obvious things had to be fixed here to make it work at all:

- **A TLS certificate issue** specific to the python.org macOS build, whose bundled certificate file is empty unless you separately run an installer script. `_ssl_context()` uses the `certifi` package's certificate bundle instead, when it's available, so this doesn't depend on the user running anything outside the project.
- **A Cloudflare block.** Groq's API sits behind Cloudflare, which silently rejects requests with no real `User-Agent` header as likely bot traffic — surfacing as a confusing `403` that has nothing to do with the API key. Setting a real `User-Agent` fixed it.

`complete()` sends one request at `temperature=0` with a fixed `seed`, matching the reproducibility standard the rest of the project holds to: no unnecessary randomness anywhere it can be avoided.

### 3. `llm/prompts.py` — teaching the model the exact grammar

`SYSTEM_PROMPT` is a full specification of the rule DSL from [05-rule-dsl.md](05-rule-dsl.md) — every opcode, every operand form, the shared-root-register requirement — plus several **worked examples**. This file grew directly out of real failures: the first live mining batch had an 84% syntax-failure rate, and every single failure traced back to one specific gap (the model tried to write a bare "just return this value" rewrite, which the grammar doesn't support — see the note in the file about the "no passthrough instruction" rule and the worked example that fixes it). A second gap (confusing boolean `not` with bitwise complement) was found and fixed the same way. Both fixes are documented directly in this file's comments, and both are reported as real findings in the paper.

### 4. `llm/miner.py` — the actual trust-boundary loop

`extract_rule_blocks()` pulls individual `rule NAME { ... }` blocks out of the model's raw text response using brace-counting (not a full parse of the whole response) — specifically so that one malformed or truncated proposal costs exactly one data point, rather than one bad rule invalidating an entire batch of otherwise-good ones.

`mine()` then runs every extracted block through **exactly the same pipeline every hand-written rule goes through**: parse it (`verify/ruledsl.py`), run it through the cheap differential-testing prefilter (`verify/difftest.py`), and if it survives, hand it to the SMT solver (`verify/smt_encode.py`). A `Proposal` dataclass records the full trip for every single candidate — did it parse, did it survive testing, what did the solver say, is it cost-reducing — so that nothing about what happened to a proposal is hidden or summarized away before it's reported.

### 5. `llm/dedupe.py` — catching re-derived duplicates

Because each mining call is a fresh, memory-less request, a model can propose the *same* underlying rule twice under two different names in two separate batches. `_canonicalize()` detects this by alpha-renaming every free register and symbolic constant to a canonical position-based name (the first register seen becomes `v0`, the first constant becomes `C0`, and so on) — two rules that are identical after this renaming are the same rule, regardless of what the model happened to call them. This isn't hypothetical: a real mining run produced the same rule three separate times under three different names, and `tests/test_dedupe.py` uses that exact real output as its test fixtures.

## Why the model never touches real code directly

This is the part worth being precise about: Module B's output is never applied to a program. It's only ever a **candidate rule**, and a candidate rule only reaches `rulelib/mined.rules` — the file the real optimizer in [04-optimizer-passes.md](04-optimizer-passes.md) actually reads — after it has independently survived the exact same differential test and SMT proof every hand-written rule must survive. The model's entire influence on the compiler is mediated by a step that says no, often.

## See it yourself

```bash
cp .env.example .env    # fill in a real GROQ_API_KEY first
python3 -m experiments.exp06_llm_miner 10
```

This makes a real network call and is the one experiment deliberately excluded from `./run_all.sh` for exactly that reason.
