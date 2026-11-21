#!/usr/bin/env bash
# Non-interactive end-to-end demonstration of SextantArc.
# Every command below is exactly what a navigator would type; nothing reads
# the terminal, nothing touches ~/.sextantarc (a temp store is used).
set -euo pipefail
cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"
TOOL="sextantarc.py"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
STORE="$WORK/store.json"

banner() { printf '\n=== %s ===\n' "$1"; }

banner "1. Version and help"
"$PYTHON" "$TOOL" --version
"$PYTHON" "$TOOL" --help | head -n 12

banner "2. Sight reduction worksheet (Sun, almanac GHA 30 / Dec 10N)"
"$PYTHON" "$TOOL" reduce \
    --body Sun \
    --gha 30 \
    --dec "10°00.0'N" \
    --lat "40°00.0'N" \
    --lon 0 \
    --ho "49°40.0'" \
    --export "$WORK/sight-sheet.txt"
printf 'sheet written: %s bytes\n' "$(wc -c < "$WORK/sight-sheet.txt")"

banner "3. Meridian altitude correction table (Sun, lower limb, height of eye 3 m)"
"$PYTHON" "$TOOL" meridian \
    --body sun \
    --limb lower \
    --lat "40°00.0'N" \
    --dec "10°00.0'N" \
    --hs "59°45.0'" \
    --ie 0 \
    --height-of-eye 3

banner "4. Chronometer log for a voyage"
"$PYTHON" "$TOOL" --file "$STORE" voyage new \
    --name "Cape Horn 2024" --notes "Falmouth to Ushuaia"
"$PYTHON" "$TOOL" --file "$STORE" chrono add \
    --voyage "Cape Horn 2024" \
    --utc 2024-06-01T12:00:00Z \
    --reading 12:00:12.5 \
    --error +12.5 --direction fast \
    --note "first comparison, Portsmouth"
"$PYTHON" "$TOOL" --file "$STORE" chrono add \
    --voyage "Cape Horn 2024" \
    --utc 2024-06-03T12:00:00Z \
    --reading 12:00:19.0 \
    --error 18.5 --direction fast \
    --note "second comparison, Biscay"
"$PYTHON" "$TOOL" --file "$STORE" chrono list
"$PYTHON" "$TOOL" --file "$STORE" chrono show --voyage "Cape Horn 2024"
"$PYTHON" "$TOOL" --file "$STORE" voyage list

banner "5. Offline self-test"
"$PYTHON" "$TOOL" --self-test

banner "Demo complete"
