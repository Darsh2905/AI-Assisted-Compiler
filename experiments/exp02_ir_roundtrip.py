"""E2 - the IR textual form round-trips.

`print -> parse -> print` must be the identity. This is not a formality: rules,
proposer prompts and the saved library are all text, so a printer and parser
that disagree would corrupt the rule library silently and the corruption would
only surface as a mysterious verification failure much later.

Tested on two populations, because each misses what the other covers:

* IR generated from the benchmark programs — realistic shapes, but only the
  shapes `irgen` happens to emit. It emits no `phi`, so a phi-printing bug
  would be invisible here.
* Randomly generated modules — covers every opcode including `phi`, `select`,
  calls and negative immediates, at the cost of being nonsense programs.
"""

from __future__ import annotations

import random

from experiments import _common as C

from frontend.parser import parse
from frontend.sema import analyze
from ir.ir import Block, Const, Function, ICMP_PREDS, Instr, Module, Reg, instr_count
from ir.irgen import generate as gen_ir
from ir.irparser import parse_module
from ir.printer import print_module

BINARY = ["add", "sub", "mul", "sdiv", "srem", "and", "or", "xor",
          "shl", "ashr", "lshr"]


def random_module(rng: random.Random, n_functions: int = 3) -> Module:
    """Structurally valid, semantically arbitrary IR covering every opcode."""
    module = Module()
    for f in range(n_functions):
        n_params = rng.randrange(0, 3)
        params = [(f"a{i}", rng.choice(["i32", "i32", "i1"]))
                  for i in range(n_params)]
        fn = Function(f"f{f}", params, rng.choice(["i32", "i1", "void"]))
        n_blocks = rng.randrange(1, 4)
        labels = ["entry"] + [f"b{i}" for i in range(1, n_blocks)]
        counter = 0
        pool: list[Reg] = [Reg(p) for p, ty in params if ty == "i32"]

        for bi, label in enumerate(labels):
            block = Block(label)
            for _ in range(rng.randrange(1, 6)):
                counter += 1
                dst = str(counter)
                kind = rng.random()
                operand = (lambda: rng.choice(pool) if pool and rng.random() < 0.6
                           else Const(rng.randrange(-(2 ** 31), 2 ** 31)))
                if kind < 0.45:
                    block.instrs.append(Instr(rng.choice(BINARY),
                                              [operand(), operand()], dst, "i32"))
                    pool.append(Reg(dst))
                elif kind < 0.58:
                    block.instrs.append(Instr("const", [Const(
                        rng.randrange(-(2 ** 31), 2 ** 31))], dst, "i32"))
                    pool.append(Reg(dst))
                elif kind < 0.70:
                    block.instrs.append(Instr("icmp", [operand(), operand()],
                                              dst, "i1",
                                              pred=rng.choice(sorted(ICMP_PREDS))))
                elif kind < 0.78:
                    block.instrs.append(Instr("alloca", [], dst, "i32",
                                              size=rng.randrange(1, 9)))
                    block.instrs.append(Instr("astore", [Reg(dst), operand(),
                                                         operand()]))
                    counter += 1
                    block.instrs.append(Instr("aload", [Reg(dst), operand()],
                                              str(counter), "i32"))
                    pool.append(Reg(str(counter)))
                elif kind < 0.88:
                    block.instrs.append(Instr("call", [operand()], dst, "i32",
                                              callee=f"f{rng.randrange(n_functions)}"))
                    pool.append(Reg(dst))
                elif bi > 0:
                    incoming = rng.randrange(1, min(3, bi) + 1)
                    block.instrs.append(Instr(
                        "phi", [operand() for _ in range(incoming)], dst, "i32",
                        labels=[labels[rng.randrange(bi)] for _ in range(incoming)]))
                    pool.append(Reg(dst))
                else:
                    block.instrs.append(Instr("sub", [operand(), operand()],
                                              dst, "i32"))
                    pool.append(Reg(dst))

            if bi + 1 < len(labels):
                if rng.random() < 0.5:
                    counter += 1
                    block.instrs.append(Instr("icmp", [Const(0), Const(1)],
                                              str(counter), "i1", pred="eq"))
                    block.instrs.append(Instr("cbr", [Reg(str(counter))],
                                              labels=[labels[bi + 1], labels[0]]))
                else:
                    block.instrs.append(Instr("br", [], labels=[labels[bi + 1]]))
            elif fn.ret_type == "void":
                block.instrs.append(Instr("ret", []))
            else:
                block.instrs.append(Instr("ret", [Const(rng.randrange(-9, 9))]))
            fn.blocks.append(block)
        module.functions.append(fn)
    return module


def check(module: Module) -> bool:
    once = print_module(module)
    twice = print_module(parse_module(once))
    return once == twice


def main() -> dict:
    bench_rows, bench_ok = [], 0
    for path in C.bench_programs():
        src = path.read_text()
        prog = parse(src)
        analyze(prog, src)
        module = gen_ir(prog)
        ok = check(module)
        bench_ok += ok
        bench_rows.append([path.name, instr_count(module), "ok" if ok else "FAIL"])

    C.heading("E2.1  Round trip on generated IR from benchmark programs")
    C.table(bench_rows, ["program", "IR instrs", "round trip"])

    trials = 500
    rng = random.Random(20260916)
    failures, opcodes, total_instrs = [], set(), 0
    for i in range(trials):
        module = random_module(rng)
        total_instrs += instr_count(module)
        opcodes |= {ins.op for fn in module.functions for ins in fn.instructions()}
        if not check(module):
            failures.append(i)

    C.heading("E2.2  Round trip on random modules")
    print(f"  modules              {trials}")
    print(f"  instructions         {total_instrs}")
    print(f"  distinct opcodes hit {len(opcodes)}: {' '.join(sorted(opcodes))}")
    print(f"  failures             {len(failures)}")

    payload = {
        "bench": {"programs": len(bench_rows), "passed": bench_ok,
                  "detail": bench_rows},
        "random": {"modules": trials, "instructions": total_instrs,
                   "opcodes": sorted(opcodes), "failures": len(failures)},
    }
    C.save("exp02_ir_roundtrip", payload)
    return payload


if __name__ == "__main__":
    main()
