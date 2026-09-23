# Documentation index

This folder explains **how the code actually works**, one subsystem per file, in plain language. If you want the big picture first, read [ARCHITECTURE.md](../ARCHITECTURE.md). If you want to run or demo the project, read [PRESENTING.md](../PRESENTING.md). This folder is for "I'm looking at `frontend/parser.py` — what is this function actually doing?"

Read them in this order if you're new to the codebase; each one builds on the last, following the same path a program takes through the compiler:

| # | File | Covers |
|---|---|---|
| 1 | [01-lexer-parser-ast.md](01-lexer-parser-ast.md) | Turning source text into a syntax tree |
| 2 | [02-semantic-analysis.md](02-semantic-analysis.md) | Type checking and structured error messages |
| 3 | [03-intermediate-representation.md](03-intermediate-representation.md) | The IR: what it looks like and how it's generated |
| 4 | [04-optimizer-passes.md](04-optimizer-passes.md) | How a proven rule actually rewrites your program |
| 5 | [05-rule-dsl.md](05-rule-dsl.md) | The language rewrite rules are written in |
| 6 | [06-smt-verification.md](06-smt-verification.md) | How a rule gets mathematically proven correct |
| 7 | [07-differential-testing.md](07-differential-testing.md) | The cheap sanity check that runs before the solver |
| 8 | [08-module-b-llm.md](08-module-b-llm.md) | How a live language model proposes rules |
| 9 | [09-experiments-and-tests.md](09-experiments-and-tests.md) | How every number in the paper gets produced and checked |
| 10 | [10-web-playground-and-cli.md](10-web-playground-and-cli.md) | The interactive tools: `provitc.py` and the web playground |

Every file follows the same shape: **what it's for**, **the key files**, **how the logic actually works step by step**, and **how to see it for yourself** (a command to run or a test to read).
