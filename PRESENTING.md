# Presenting this project

A practical guide for running and demoing ProveIt-C — to a class, a reviewer, or yourself six months from now. Two ways to show it: a **web playground** you drive live in a browser, or a **scripted terminal walkthrough**. Use the web playground if you can; it's the more convincing demo.

## 1. One-time setup

```bash
git clone https://github.com/Darsh2905/AI-Assisted-Compiler.git
cd AI-Assisted-Compiler
pip install -r requirements.txt
```

That's it for the core project — Z3 is the only dependency. Confirm it works:

```bash
python3 -m pytest        # should print "218 passed"
./run_all.sh              # regenerates every number in the paper, exits 0
```

If either of those fails, something is wrong with the environment, not the demo — fix it before presenting.

**Optional, only if you also want to show Module B (the live LLM proposer):**

```bash
cp .env.example .env
# edit .env and paste in a Groq API key (free at console.groq.com/keys)
```

Nothing else in the project needs this. Skip it entirely if you're not planning to run a live mining batch.

## 2. The recommended demo: the web playground

```bash
python3 web/server.py
```

This opens `http://localhost:8765` in your browser automatically. It's a real, live compiler frontend — you paste code, it runs the actual pipeline (not a mockup), and shows you every stage.

### What program to paste in

Use the **`scale.mc`** sample from the dropdown — it's short (8 lines) but touches every part of the pipeline:

```c
int shift_scale(int x, int n) {
  int doubled = x + x;
  int scaled  = doubled * 16;
  if (n != 0 && scaled / n > 10) {
    return scaled / n;
  }
  return scaled;
}
```

### Walking through the tabs

1. **Tokens** — the raw text broken into words, each with an exact `line:col` span. Point out that this span is what lets every later error message point at the *exact* broken character, not just a line number.

2. **AST** — the same code as a tree instead of flat text. Point at `Binary && : bool` — the `: bool` is proof the type checker already ran and confirmed this expression is well-typed.

3. **IR** — your code translated into simple, one-operation-per-line instructions. Point at the `^sc.rhs` / `^sc.short` blocks — this is the compiler proving that `n != 0 && scaled/n > 10` will *never* evaluate the division when `n` is zero, because that division is defined to trap in this language, and a trap is observable behavior a correct compiler must never accidentally trigger.

4. **Optimized** — click it and look at the diff. `mul %_, 16` becomes `shl %_, 4`. Say: "This is the compiler actually rewriting the program, not just analyzing it."

5. **Rules fired** — names the exact rule that caused that change (`mul_pow2_to_shl`), plus a one-line justification.

6. **Verifier** — the payoff tab. Scroll to any `REFUTED` row and read its counterexample. Say: "Every rule here was checked against all 4 billion possible 32-bit inputs, not a sample. Green means mathematically proven safe. Red means the solver found the exact number that breaks it, and shows it to you."

### A second act: show it catching mistakes

Switch the dropdown to **`broken.mc`** and hit Compile. Five deliberate type errors, each with a caret pointing at the exact wrong text — good if someone asks "what happens when the code is wrong?"

## 3. The alternative: scripted terminal walkthrough

If you'd rather not click through live (e.g. presenting over a call, or want something scriptable), run:

```bash
./demo/demo.sh          # press Enter between each of 10 sections
./demo/demo.sh --auto   # runs straight through, ~4 seconds, no pauses
./demo/demo.sh --fast   # runs through with short automatic pauses, good for recording
```

It tells the same story as the web playground, in the same order, with narration built in.

## 4. If you want to show Module B (the live LLM proposer)

This requires the `.env` setup from step 1. Run:

```bash
python3 -m experiments.exp06_llm_miner 10
```

This makes a real API call to Groq, asks a model to propose 10 rewrite rules in ProveIt-C's rule grammar, and runs every single proposal through the same parse → differential-test → SMT pipeline as everything else in the project. Expect to see some proposals rejected (malformed syntax or a real logic bug the solver catches) and some accepted into `rulelib/mined.rules`. This is the one part of the project that costs an API call and touches the network — everything else runs fully offline.

If someone asks "so where's the AI in all this?" — this is the honest answer: **the model proposes rules, never touches a real program directly, and its proposals only ever reach real code after they've survived a differential test and a formal proof.**

## 5. The paper

`paper/paper.pdf` is the full IEEE-format writeup — 10 pages, positions the project against the closest published systems (LLM-Vectorizer, LLM-VeriOpt, CoV), and reports every number shown in the demo plus the ones that don't fit in a UI (the full detection-rate table, the solver-timeout characterization, etc.).

To rebuild it from source after any edit:

```bash
cd paper
tectonic -X compile paper.tex --outdir .
# or, with a traditional TeX distribution, run pdflatex three times:
# pdflatex paper.tex && pdflatex paper.tex && pdflatex paper.tex
```

## 6. Questions you'll probably get, answered in one line each

- **"Is this a real compiler or a proof of concept?"** Real, working front end through IR generation and optimization; no native backend yet (see the status table in [README.md](README.md)).
- **"Does the AI ever touch the real code?"** No — it only ever proposes *rules*, which must survive testing and formal proof before the compiler's pattern-matcher can apply them to real programs.
- **"What happens if the AI proposes something wrong?"** It gets caught — either by the cheap differential test or, if that misses it, by the SMT solver, which checks all 4 billion possible 32-bit inputs rather than a sample.
- **"How do you know the checker itself isn't buggy?"** Two independent implementations of the same semantics (`verify/smt_encode.py` and `verify/interp.py`) cross-check every counterexample the solver produces — see `tests/test_verify.py`.
- **"What's `MiniC`?"** A small C-like language designed specifically so every operation is fully defined (wraps or traps, nothing is ever undefined) — that's what makes formal verification of rewrites possible at all. See `spec/semantics.md`.

## 7. If something breaks mid-demo

- **Web server won't start / port in use:** `lsof -ti:8765 | xargs kill`, then re-run `python3 web/server.py`.
- **`pytest` fails:** run `pip install -r requirements.txt` again — something likely didn't install.
- **Module B call fails:** almost certainly the `.env` file is missing or the key is wrong; everything else in the project works with zero setup and no network, so just skip that part of the demo.
