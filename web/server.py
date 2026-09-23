#!/usr/bin/env python3
"""ProveIt-C local playground: a thin HTTP wrapper around the real compiler.

    python3 web/server.py [PORT]        # default port 8765

Opens a browser at http://localhost:8765. Paste MiniC source, click Compile,
and every panel on the page is produced by the actual pipeline in this
repository -- the same `frontend/`, `ir/`, `passes/` and `verify/` modules the
CLI and the test suite use. There is no separate "demo" implementation to
drift out of sync with the compiler: this file only serializes what those
modules already return.

Stdlib only, no new dependency. Runs locally; nothing here is published to
claude.ai, because the real backend needs Z3 and a Python process, which a
static Artifact page cannot provide.
"""

from __future__ import annotations

import difflib
import glob
import io
import json
import sys
import webbrowser
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from frontend.diagnostics import CompileError                  # noqa: E402
from frontend.lexer import LexError, tokenize                  # noqa: E402
from frontend.parser import parse                               # noqa: E402
from frontend.sema import analyze                               # noqa: E402
from ir.ir import instr_count                                   # noqa: E402
from ir.irgen import generate                                   # noqa: E402
from ir.printer import print_module                             # noqa: E402
from passes.peephole import classify, optimize, side_cost       # noqa: E402
from provitc import ast_lines                                   # noqa: E402
from verify.difftest import run_difftest                        # noqa: E402
from verify.ruledsl import load_rules                           # noqa: E402
from verify.smt_encode import verify_rule                       # noqa: E402

APP_HTML = (Path(__file__).parent / "app.html").read_bytes()

SAMPLE_FILES = (
    sorted((ROOT / "demo").glob("*.mc"))
    + sorted((ROOT / "bench").glob("*.mc"))
)


# --------------------------------------------------------------- one-time --
# The rule library is proven once, at server start, not on every request:
# each proof is a real Z3 call, and re-running ~35 of them per keystroke
# would make the page feel like it is waiting on a network call it doesn't
# need to make. Compiling a program against this library still runs no
# model and touches no network -- the proofs happened once, offline, exactly
# as the paper describes.

def _verify_all() -> list[dict]:
    out = []
    for path in sorted(glob.glob(str(ROOT / "rulelib" / "*.rules"))):
        for rule in load_rules(path):
            # rulelib/hard.rules deliberately contains queries whose verdict
            # is timing-dependent (see spec/semantics.md and the paper's
            # Section IV-F); give the solver less budget here so a live demo
            # can never stall on one of them, and record what happened rather
            # than hide it.
            timeout = 2_000 if rule.origin == "hard" else 10_000
            v = verify_rule(rule, timeout_ms=timeout)
            out.append({
                "name": rule.name,
                "file": Path(path).name,
                "origin": rule.origin,
                "note": rule.note,
                "expect": rule.expect,
                "status": v.status,
                "solve_ms": round(v.solve_ms, 2),
                "counterexample": v.counterexample,
                "detail": v.detail,
                "match_cost": side_cost(rule.match),
                "rewrite_cost": side_cost(rule.rewrite),
            })
    return out


print("Verifying the rule library (one-time, offline)...", file=sys.stderr)
ALL_VERDICTS = _verify_all()
PROVEN_RULES = [
    r for r in load_rules(str(ROOT / "rulelib" / "textbook.rules"))
    if any(v["name"] == r.name and v["status"] == "PROVEN" for v in ALL_VERDICTS)
]
PROFITABLE, HELD_BACK = classify(PROVEN_RULES)
print(f"  {len(ALL_VERDICTS)} rules checked, "
      f"{len(PROVEN_RULES)} proven, {len(PROFITABLE)} applied by default",
      file=sys.stderr)


# ------------------------------------------------------------- pipeline ----

def run_pipeline(source: str, do_optimize: bool) -> dict:
    """Run the real compiler on `source` and collect every stage's output.

    Nothing here re-implements a stage. It calls the same functions
    `provitc.py` calls and packages the results as JSON.
    """
    result: dict = {
        "tokens": None, "lex_error": None,
        "diagnostics": [], "ok": False,
        "ast": None, "ir": None, "ir_count": None,
        "optimized_ir": None, "optimized_count": None,
        "rules_applied": {}, "rules_notes": {}, "diff": None,
    }

    try:
        result["tokens"] = [
            {"kind": t.kind, "text": t.text, "line": t.span.line,
             "col_start": t.span.col_start, "col_end": t.span.col_end}
            for t in tokenize(source) if t.kind != "eof"
        ]
    except LexError as e:
        result["lex_error"] = {"message": e.message, "line": e.span.line,
                               "col": e.span.col_start}
        return result

    try:
        prog = parse(source)
        analyze(prog, source)
    except CompileError as exc:
        result["diagnostics"] = [
            {"code": d.code, "title": d.title,
             "rendered": d.render(source, "input.mc"),
             "context": d.to_context(source)}
            for d in exc.diagnostics
        ]
        return result

    result["ok"] = True
    result["ast"] = "\n".join(ast_lines(prog))

    module = generate(prog)
    ir_before = print_module(module)
    result["ir"] = ir_before
    result["ir_count"] = instr_count(module)

    if do_optimize:
        stats = optimize(module, PROVEN_RULES)
        ir_after = print_module(module)
        result["optimized_ir"] = ir_after
        result["optimized_count"] = instr_count(module)
        result["rules_applied"] = stats.applied
        result["rules_notes"] = {
            r.name: r.note for r in PROFITABLE if r.name in stats.applied
        }
        if ir_after != ir_before:
            result["diff"] = "\n".join(difflib.unified_diff(
                ir_before.splitlines(), ir_after.splitlines(),
                fromfile="before -O", tofile="after -O", lineterm=""))

    return result


# ------------------------------------------------------------------ HTTP --

class Handler(BaseHTTPRequestHandler):
    server_version = "ProveItC/0.1"

    def log_message(self, fmt, *args):        # quieter default logging
        sys.stderr.write(f"  {self.address_string()} {fmt % args}\n")

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(self, body: bytes, content_type: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):                          # noqa: N802 (stdlib name)
        if self.path in ("/", "/index.html"):
            self._send_bytes(APP_HTML, "text/html; charset=utf-8")
        elif self.path == "/api/samples":
            samples = {p.stem: p.read_text() for p in SAMPLE_FILES}
            self._send_json(samples)
        elif self.path == "/api/verifier":
            self._send_json({
                "verdicts": ALL_VERDICTS,
                "proven": len(PROVEN_RULES),
                "applied": len(PROFITABLE),
                "held_back": [r.name for r in HELD_BACK],
            })
        else:
            self._send_json({"error": "not found"}, status=404)

    def do_POST(self):                          # noqa: N802
        if self.path != "/api/compile":
            self._send_json({"error": "not found"}, status=404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._send_json({"error": "malformed request"}, status=400)
            return
        source = body.get("source", "")
        do_optimize = bool(body.get("optimize", True))
        if not source.strip():
            self._send_json({"error": "empty source"}, status=400)
            return
        try:
            self._send_json(run_pipeline(source, do_optimize))
        except Exception as exc:                # noqa: BLE001 - report, don't hang
            self._send_json({"error": f"internal error: {exc}"}, status=500)


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"\nProveIt-C playground running at {url}")
    print("Press Ctrl+C to stop.\n")
    try:
        webbrowser.open(url)
    except Exception:                            # noqa: BLE001 - headless env
        pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")


if __name__ == "__main__":
    main()
