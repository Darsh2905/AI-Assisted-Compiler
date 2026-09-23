"""Shared plumbing for the experiment scripts.

Every number that appears in the paper, the README or the status page is
produced by one of these scripts and written to `results/*.json`. Nothing is
typed in by hand.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def bench_programs() -> list[pathlib.Path]:
    return sorted((ROOT / "bench").glob("*.mc"))


def rule_files() -> list[pathlib.Path]:
    return sorted((ROOT / "rulelib").glob("*.rules"))


def hand_written_rule_files() -> list[pathlib.Path]:
    """`rule_files()` minus anything mined by Module B.

    E3, E4 and E5 use this, not `rule_files()`, so that every number already
    published in the paper and README stays exactly reproducible regardless
    of whether `rulelib/mined.rules` exists on disk -- a live LLM proposer
    adding a new file to `rulelib/` must never silently change a number that
    was reported before that file existed. The Module B experiment
    (`exp06_llm_miner.py`) reports the with-mined-rules comparison
    explicitly and separately, never by mutating these.
    """
    return [p for p in rule_files() if p.name != "mined.rules"]


def save(name: str, payload: dict) -> pathlib.Path:
    RESULTS.mkdir(exist_ok=True)
    path = RESULTS / f"{name}.json"
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
        fh.write("\n")
    return path


def load(name: str) -> dict:
    with open(RESULTS / f"{name}.json", encoding="utf-8") as fh:
        return json.load(fh)


def heading(text: str) -> None:
    print()
    print(text)
    print("=" * len(text))


def table(rows: list[list], headers: list[str]) -> None:
    cells = [[str(c) for c in row] for row in rows]
    widths = [max(len(headers[i]), *(len(r[i]) for r in cells)) if cells
              else len(headers[i]) for i in range(len(headers))]
    print("  ".join(h.ljust(w) for h, w in zip(headers, widths)).rstrip())
    print("  ".join("-" * w for w in widths))
    for row in cells:
        print("  ".join(c.ljust(w) for c, w in zip(row, widths)).rstrip())


def pct(a: int, b: int) -> str:
    return "n/a" if b == 0 else f"{a / b:.1%}"
