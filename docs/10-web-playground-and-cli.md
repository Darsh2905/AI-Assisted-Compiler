# 10. The interactive tools: CLI and web playground

**What this is for:** the two ways to actually drive the compiler by hand — a command-line tool for scripting and quick checks, and a local web app for a live, presentable demo. Both call the exact same underlying code as everything else in this project; neither is a separate reimplementation.

**Key files:** `provitc.py`, `web/server.py`, `web/app.html`

## `provitc.py`: the command-line tool

A single `argparse`-based script with one subcommand per pipeline stage: `tokens`, `ast`, `check`, `context`, `ir`, `verify`, `library`. `main()` dispatches on `args.command` and calls straight into the same functions the rest of the docs describe — `frontend.parser.parse()`, `frontend.sema.analyze()`, `ir.irgen.generate()`, and so on. The one piece of logic that lives only here (not duplicated anywhere else) is `proven_library()`, which globs every `.rules` file, verifies each one fresh, and returns only the ones that come back `PROVEN` — this is what `./provitc.py ir file.mc -O` and the web playground both use to decide what's actually safe to apply.

`dump_ast()` (and the `ast_lines()` function it's built from) is worth a specific mention: `ast_lines()` returns the tree as a list of strings instead of printing directly, so that both the CLI (`dump_ast()` just prints each line) and the web playground (which needs the tree as a single string to send as JSON) render the *exact same text* from one shared function — nothing about the tree's textual form can drift between the two tools.

## `web/server.py`: a real backend, not a mockup

This is a plain Python `http.server` (standard library only — no Flask, no new dependency) with four routes:

- `GET /` — serves the page (`web/app.html`)
- `GET /api/samples` — returns the contents of every sample `.mc` file
- `GET /api/verifier` — returns the full verification table (computed **once**, when the server starts, not on every request — see below)
- `POST /api/compile` — the main endpoint: runs `run_pipeline()` on whatever source text it's given

`run_pipeline()` is the important function to read if you want to understand what the playground actually does: it calls `tokenize()`, then `parse()` + `analyze()` (catching `CompileError` and returning the diagnostics as JSON if the program is invalid), then `generate()` for IR, then `optimize()` if optimization was requested — and packages every intermediate result into one JSON response. This is the same reason `ast_lines()` is shared: **the playground shows real output from real compiler code, not a separately-written approximation of what the compiler does.**

### Why verification happens once at startup, not per request

Proving a rule calls Z3, and Z3 calls are not free. `_verify_all()` runs once, when `python3 web/server.py` starts, verifies every rule in `rulelib/`, and caches the results in memory (`ALL_VERDICTS`, `PROVEN_RULES`, `PROFITABLE`). Every subsequent `/api/compile` request reuses that already-proven library — exactly mirroring the real architecture's own claim that proving is expensive and offline, while applying a rule at compile time is just a fast pattern match. If verification ran on every keystroke, the playground would misrepresent its own design.

## `web/app.html`: the front end

A single HTML file with inline CSS and JavaScript (no build step, no framework) — a textarea for source code, a sample-program dropdown populated from `/api/samples`, and a set of tabs (Tokens, AST, Diagnostics, IR, Optimized, Rules fired, Verifier) that render whatever JSON `/api/compile` most recently returned. The `renderDiff()` function is the one doing real work worth knowing about: it takes the unified diff `run_pipeline()` computes with Python's `difflib` and colors added/removed lines directly, so the "what changed" view in the Optimized tab is a real diff of real before/after IR text, not a hand-summarized description of it.

## See it yourself

```bash
./provitc.py ir demo/scale.mc -O       # the CLI path
python3 web/server.py                  # the web path — opens a browser automatically
```

See [PRESENTING.md](../PRESENTING.md) for a full walkthrough of using the web playground in a live demo.
