#!/usr/bin/env bash
# Everything, in one command. Exits non-zero if any assertion or any declared
# rule verdict regresses -- "the script did not crash" is not a passing result.
set -euo pipefail
cd "$(dirname "$0")"

echo "=== dependencies ==="
python3 -c "import z3; print('z3', z3.get_version_string())"

echo
echo "=== grammar: LL(1) analysis ==="
python3 spec/grammar.py

echo
echo "=== tests ==="
python3 -m pytest -q

echo
echo "=== rule library: declared verdicts ==="
python3 provitc.py verify

echo
echo "=== experiments ==="
python3 -m experiments.run_all

echo
echo "OK"
