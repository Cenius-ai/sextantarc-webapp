#!/usr/bin/env bash
# SextantArc install: project-level dependencies only, no system packages.
# Idempotent; installs, checks, and EXITS (it never starts anything long-lived).
set -euo pipefail
cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"

echo "==> Upgrading pip, setuptools and wheel"
"$PYTHON" -m pip install --upgrade pip setuptools wheel

echo "==> Installing project dependencies (requirements.txt)"
"$PYTHON" -m pip install -r requirements.txt

echo "==> Installing the sextantarc console script (editable)"
"$PYTHON" -m pip install -e .

echo "==> Import self-check"
"$PYTHON" -c "import sextantarc; print('sextantarc', sextantarc.APP_VERSION, 'imports cleanly')"

echo "==> Version self-check"
"$PYTHON" sextantarc.py --version

echo
echo "Setup complete."
echo "Run:  python3 sextantarc.py --help"
echo "      (or the installed 'sextantarc' command)"
echo "Demo: sh demo.sh"
echo "Test: python3 -m pytest -q"
