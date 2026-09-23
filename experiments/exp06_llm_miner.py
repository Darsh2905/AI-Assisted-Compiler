"""E6 - Module B: what happens when a real model proposes rewrite rules.

This is the experiment the paper's abstract says does not exist yet. Every
other experiment in this project measures the verifier against rules a human
wrote, some correct and some deliberately wrong. This one measures it against
rules an actual language model proposes, unscripted, and reports the number
the whole project was built to produce:

    of the proposals that are syntactically valid and pass the cheap
    differential-testing prefilter, what fraction does the SMT solver still
    refute?

Unlike every other experiment script, this one makes a real network call and
is therefore NOT part of `./run_all.sh` -- that script's whole point is a
zero-network regression check, and a live API dependency would break that
property for everything else in it. Run this one explicitly:

    python3 -m experiments.exp06_llm_miner [N]

N defaults to 25. Requires GROQ_API_KEY (see .env.example). The raw model
response is cached to results/llm_cache/ keyed by a hash of the exact prompt
sent, so re-running with the same N does not re-spend an API call unless
--refresh is passed -- the same "cache every LLM response by prompt hash"
reproducibility rule the project's own design docs call for elsewhere.
"""

from __future__ import annotations

import hashlib
import sys

from experiments import _common as C

from llm.dedupe import deduplicate
from llm.groq_client import GroqClient, GroqError
from llm.miner import mine
from llm.prompts import SYSTEM_PROMPT, build_user_prompt
from frontend.parser import parse
from frontend.sema import analyze
from ir.irgen import generate as gen_ir
from ir.ir import instr_count
from passes.peephole import optimize
from verify.ruledsl import load_rules
from verify.smt_encode import verify_rule

CACHE_DIR = C.ROOT / "results" / "llm_cache"
MINED_RULES_FILE = C.ROOT / "rulelib" / "mined.rules"


def existing_rule_names() -> set[str]:
    return {r.name for path in C.rule_files() for r in load_rules(str(path))}


def _cache_key(model: str, n: int, existing: set[str]) -> str:
    blob = f"{model}|{n}|{','.join(sorted(existing))}|{SYSTEM_PROMPT}".encode()
    return hashlib.sha256(blob).hexdigest()[:16]


def get_proposal_text(client: GroqClient, n: int, existing: set[str],
                      refresh: bool) -> tuple[str, bool]:
    """Returns (raw_text, was_cached)."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    key = _cache_key(client.model, n, existing)
    cache_file = CACHE_DIR / f"{key}.txt"
    if cache_file.exists() and not refresh:
        return cache_file.read_text(), True

    user_prompt = build_user_prompt(n, existing)
    # The Groq reasoning models (gpt-oss-*) spend tokens on an internal
    # "reasoning" field before ever emitting the answer content, and that
    # field is not size-bounded by how long the actual rule text will be --
    # a low max_tokens here silently truncates to an empty response, not a
    # short one. Generous headroom, not exactness, is what avoids that.
    completion = client.complete(SYSTEM_PROMPT, user_prompt, temperature=0.0,
                                 max_tokens=8000, seed=0)
    cache_file.write_text(completion.text)
    C.heading("Live API call")
    print(f"  model              {completion.model}")
    print(f"  prompt tokens      {completion.prompt_tokens}")
    print(f"  completion tokens  {completion.completion_tokens}")
    print(f"  cached to          {cache_file.relative_to(C.ROOT)}")
    return completion.text, False


def main() -> dict:
    n = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 25
    refresh = "--refresh" in sys.argv

    try:
        client = GroqClient()
    except (RuntimeError,) as e:
        print(f"\n{e}\n", file=sys.stderr)
        sys.exit(1)

    existing = existing_rule_names()
    print(f"Requesting {n} candidate rules from {client.model} "
          f"({len(existing)} existing names to avoid)...")

    try:
        raw_text, cached = get_proposal_text(client, n, existing, refresh)
    except GroqError as e:
        print(f"\nGroq API error: {e}\n", file=sys.stderr)
        sys.exit(1)

    if cached:
        print(f"(using cached response — pass --refresh to force a new call)")

    run = mine(lambda _n: raw_text, n=n, existing_rule_names=existing)

    C.heading("E6.1  Every proposal, every verdict")
    rows = []
    for p in run.proposals:
        if not p.parse_ok:
            verdict = "MALFORMED"
        elif p.difftest_verdict == "REFUTED":
            verdict = "difftest-REFUTED"
        elif p.smt_status is None:
            verdict = "?"
        else:
            verdict = p.smt_status
        accepted = "accepted" if p.accepted else ""
        rows.append([p.rule_name, verdict, f"{p.solve_ms:.1f}" if p.solve_ms else "-",
                    accepted])
    C.table(rows, ["rule", "verdict", "solve ms", ""])

    s = run.summary()
    C.heading("E6.2  Summary")
    print(f"  requested                                    {s['requested']}")
    print(f"  extracted as rule blocks                     {s['extracted']}")
    print(f"  extra prose outside rule blocks               {s['extra_text_present']}")
    print(f"  malformed (did not parse)                    {s['malformed']} "
          f"({C.pct(s['malformed'], s['extracted'])})")
    print(f"  parsed successfully                          {s['parsed']}")
    print(f"  refuted by the cheap differential prefilter  {s['difftest_refuted']}")
    print(f"  survived the prefilter                       {s['survived_difftest']}")
    print(f"  >>> of those, refuted by the SMT solver <<<  "
          f"{s['smt_refuted_after_surviving_difftest']} "
          f"({C.pct(s['smt_refuted_after_surviving_difftest'], s['survived_difftest'])})")
    print(f"  proven                                       {s['proven']}")
    print(f"  proven AND cost-reducing (accepted)          {s['accepted']}")

    accepted_rules = [p for p in run.proposals if p.accepted]
    if accepted_rules:
        C.heading("E6.3  Rules accepted into rulelib/mined.rules")
        existing_mined = MINED_RULES_FILE.read_text() if MINED_RULES_FILE.exists() else ""
        with open(MINED_RULES_FILE, "a", encoding="utf-8") as fh:
            if not existing_mined:
                fh.write("// Rules mined from a live LLM proposer (Module B), "
                        "kept separate from\n// the hand-written rulelib "
                        "files so provenance is never ambiguous.\n// "
                        "Generated by experiments/exp06_llm_miner.py.\n\n")
            for p in accepted_rules:
                fh.write(p.rule.render() + "\n\n")
                print(f"  + {p.rule_name}")
        print(f"\n  appended to {MINED_RULES_FILE.relative_to(C.ROOT)}")

    dedup = benchmark_impact() if MINED_RULES_FILE.exists() else None

    payload = {
        "n_requested": n, "model": client.model, "used_cache": cached,
        "summary": s,
        "proposals": [p.to_dict() for p in run.proposals],
        "benchmark_impact": dedup,
    }
    C.save("exp06_llm_miner", payload)
    return payload


def benchmark_impact() -> dict:
    """Compare the hand-written baseline library against hand-written +
    everything Module B has mined so far, on the same benchmark suite
    E5 uses.

    This never touches E5's own numbers (see `hand_written_rule_files()`) --
    it is a new, explicitly separate, explicitly labeled comparison, exactly
    the way E4 reports uniform vs. boundary-seeded testing side by side
    rather than blending them into one number.
    """
    # `optimize()` performs no verification of its own -- it trusts whatever
    # rule list it is handed, exactly as `passes/peephole.py` documents. Only
    # `rulelib/*.rules` files that DECLARE `expect proven` are meant to reach
    # it; `adversarial.rules` and `stealth.rules` are refuted on purpose and
    # must never be applied to real code. Filtering to `verify_rule(...).is_proof`
    # here mirrors exactly what `exp05_rule_application.py` does before it
    # ever calls `optimize()` -- skipping this step would let a deliberately
    # wrong rule silently rewrite the benchmark suite.
    hand_written_candidates = [r for path in C.hand_written_rule_files()
                               for r in load_rules(str(path))]
    hand_written = [r for r in hand_written_candidates if verify_rule(r).is_proof]

    mined_raw = load_rules(str(MINED_RULES_FILE))
    mined_candidates, dropped = deduplicate(mined_raw)
    mined = [r for r in mined_candidates if verify_rule(r).is_proof]
    if len(mined) != len(mined_candidates):
        # Everything in mined.rules was proven at acceptance time (see
        # mine()); a mismatch here would mean the file was hand-edited or a
        # rule was appended without going through the acceptance gate.
        unsound = {r.name for r in mined_candidates} - {r.name for r in mined}
        raise RuntimeError(
            f"rulelib/mined.rules contains rule(s) that do not currently "
            f"verify as PROVEN: {sorted(unsound)} -- refusing to apply them")
    combined = hand_written + mined

    def compile_all(rules):
        before = after = 0
        for path in C.bench_programs():
            src = path.read_text()
            prog = parse(src)
            analyze(prog, src)
            module = gen_ir(prog)
            before += instr_count(module)
            optimize(module, rules)
            after += instr_count(module)
        return before, after

    C.heading("E6.4  Impact on the benchmark suite: hand-written vs. +mined")
    print(f"  hand-written, proven (E5's own number)  {len(hand_written)} rules")
    print(f"  + mined.rules: raw accepted / distinct / proven-now  "
          f"{len(mined_raw)} / {len(mined_candidates)} / {len(mined)}")
    print(f"  = combined library                       {len(combined)} rules\n")

    b0, a0 = compile_all(hand_written)
    b1, a1 = compile_all(combined)
    assert b0 == b1, "the same source compiled to a different instruction count"
    C.table([
        ["hand-written only (E5's own number)", b0, a0, f"{(b0 - a0) / b0:.1%}"],
        ["hand-written + mined", b1, a1, f"{(b1 - a1) / b1:.1%}"],
    ], ["library", "before", "after", "reduction"])

    return {
        "hand_written_rules_proven": len(hand_written),
        "mined_raw_accepted": len(mined_raw),
        "mined_distinct": len(mined_candidates),
        "mined_still_proven": len(mined),
        "duplicates_dropped": dropped,
        "instr_before": b0,
        "hand_written_after": a0,
        "combined_after": a1,
    }


if __name__ == "__main__":
    main()
