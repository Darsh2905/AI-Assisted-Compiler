#!/usr/bin/env bash
# ProveIt-C live demo.
#
#   ./demo/demo.sh          step through, press Enter between sections
#   ./demo/demo.sh --auto    run straight through (for recording)
#   ./demo/demo.sh --fast    auto, with short pauses so it stays readable
#
# Ten beats, about five minutes: one program walked from source text to
# optimised IR, then the proof that authorised the optimisation, then what the
# same checker refuses, and finally the measurement that motivates the whole
# design.
set -uo pipefail
cd "$(dirname "$0")/.."

MODE="${1:-step}"

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
  B=$'\033[1m'; DIM=$'\033[2m'; R=$'\033[0m'
  CY=$'\033[36m'; GR=$'\033[32m'; RD=$'\033[31m'; YL=$'\033[33m'; MG=$'\033[35m'
else
  B=''; DIM=''; R=''; CY=''; GR=''; RD=''; YL=''; MG=''
fi

STEP=0
pause() {
  case "$MODE" in
    --auto) : ;;
    --fast) sleep 1.6 ;;
    *) printf "\n${DIM}   [Enter] ${R}"; read -r _ ;;
  esac
}

beat() {
  STEP=$((STEP + 1))
  printf "\n\n${CY}${B}━━━ %d. %s ${R}\n" "$STEP" "$1"
  [ $# -gt 1 ] && printf "${DIM}    %s${R}\n" "$2"
  printf "\n"
}

cmd() {
  printf "${DIM}    \$ ${R}${B}%s${R}\n\n" "$*"
  eval "$@" 2>&1 | sed 's/^/    /'
}

say() { printf "\n${YL}    %s${R}\n" "$*"; }

clear 2>/dev/null || true
cat <<BANNER
${B}${MG}
    ProveIt-C
${R}${DIM}    a compiler where the AI proposes and a verifier decides
    ~30% built  ·  184 tests  ·  Z3 5.1.0${R}
BANNER
pause

# ---------------------------------------------------------------------------
beat "The program" "eight lines, chosen so every stage has something to show"
cmd "cat -n demo/scale.mc"
pause

# ---------------------------------------------------------------------------
beat "Lexer" "every token carries a source span — this is what makes a diagnostic patchable"
cmd "./provitc.py tokens demo/scale.mc | head -14"
say "line:col-col on each token. Spans survive all the way to the error messages."
pause

# ---------------------------------------------------------------------------
beat "Parser + type checker" "hand-written recursive descent; the grammar is proven conflict-free LL(1)"
cmd "./provitc.py ast demo/scale.mc | head -26"
say "The ': int' and ': bool' annotations are filled in by semantic analysis."
say "Note '&&' typed bool — MiniC has no implicit int-to-bool conversion."
pause

# ---------------------------------------------------------------------------
beat "When the program is wrong" "five faults in one pass, each with a span and a caret"
cmd "./provitc.py check demo/broken.mc"
say "It accumulates faults instead of stopping at the first."
pause

# ---------------------------------------------------------------------------
beat "The same errors, as structured data" "this is what a model would be handed — compiler state, not prose"
cmd "./provitc.py context demo/broken.mc | head -30"
say "expected / found / in_scope with types. No model produced any of this."
pause

# ---------------------------------------------------------------------------
beat "IR generation" "three-address, basic blocks, and the textual form round-trips exactly"
cmd "./provitc.py ir demo/scale.mc | head -30"
say "^sc.rhs / ^sc.short are the short-circuit blocks for '&&'."
say "That matters: 'n != 0 && scaled/n > 10' must NOT evaluate the divide when n is 0."
pause

# ---------------------------------------------------------------------------
beat "Optimise against the proven rule library" "no model runs here — this is a pattern match"
cmd "./provitc.py ir demo/scale.mc -O | head -1"
printf "\n${DIM}    every line the optimiser changed:${R}\n\n"
diff <(./provitc.py ir demo/scale.mc) \
     <(./provitc.py ir demo/scale.mc -O | tail -n +2) \
  | grep -v '^---$' \
  | sed -e "s/^</    ${RD}-/" -e "s/^>/    ${GR}+/" -e "s/$/${R}/" \
        -e 's/^\([0-9]\)/    '"${DIM}"'\1/'
say "The multiply and its constant are gone; a shift by 4 took their place."
say "Run it twice and you get byte-identical IR — there is no model in this path."
pause

# ---------------------------------------------------------------------------
beat "Why was the compiler allowed to do that?" "because a solver proved it, for every power of two at once"
cmd "sed -n '/^rule mul_pow2_to_shl/,/^}/p' rulelib/textbook.rules"
printf "\n"
cmd "./provitc.py verify rulelib/textbook.rules | grep mul_pow2_to_shl"
say "C is SYMBOLIC. One unsat covers all 2^32 values of x and every power-of-two C."
say "That generality is what lets the model stay out of the build."
pause

# ---------------------------------------------------------------------------
beat "And what it refuses" "the checker has to reject things, or it proves nothing"
cmd "./provitc.py verify rulelib/adversarial.rules | grep -E 'sdiv2_to_ashr|cmp_via_subtraction|shl_mask_trap_erasure'"
say "x/2 -> x>>1 fails at x = -1: sdiv truncates toward zero, ashr toward -inf."
printf "${RD}${B}"
say "shl_mask_trap_erasure is the interesting one. Masking a shift to 5 bits is"
say "exactly what the hardware does. It is value-correct on EVERY input."
say "It is refuted only because it deletes a trap."
printf "${R}"
pause

# ---------------------------------------------------------------------------
beat "The punchline" "why a proof and not just a very large test suite"
printf "${DIM}    a rewrite that is wrong on 1 input in 4.3 billion:${R}\n\n"
python3 - <<'PY' 2>&1 | sed 's/^/    /'
from verify.ruledsl import load_rules
from verify.difftest import run_difftest
from verify.smt_encode import verify_rule
rules = {r.name: r for r in load_rules('rulelib/stealth.rules')}

r = rules['lt_neg_self']
print(f"  rule: x < -x   rewritten to   x < 0")
for n in (1_000, 100_000, 1_000_000):
    d = run_difftest(r, trials=n, seed=1, boundary_rate=0.0)
    print(f"    random testing, {n:>9,} trials   ->  {d.verdict}")
v = verify_rule(r)
print(f"    SMT solver                          ->  {v.status}"
      f"  (x = -2147483648, in {v.solve_ms:.0f} ms)")

c = rules['mul_identity_with_magic_constant']
print()
print("  and one wrong only for the constant 0x5A5A5A5A:")
d = run_difftest(c, trials=100_000, seed=1, boundary_rate=0.5)
print(f"    boundary-seeded testing, 100,000 trials -> {d.verdict}")
v = verify_rule(c)
print(f"    SMT solver                             -> {v.status} in {v.solve_ms:.0f} ms")
PY
say "More testing does not close this gap. The solver does not sample."
pause

# ---------------------------------------------------------------------------
printf "\n\n${GR}${B}━━━ Everything above is reproducible ${R}\n\n"
printf "${DIM}    python3 -m pytest -q             184 assertions, every one with a threshold
    python3 -m experiments.run_all   regenerates every number in the paper
    ./run_all.sh                     both; non-zero exit on any regression
${R}\n"
printf "${DIM}    status page   https://claude.ai/artifact/JtmG9ve444JczMKztEkDR6
    paper         paper/paper.pdf   (8 pages, IEEE two-column)${R}\n\n"
