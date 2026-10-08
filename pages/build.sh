#!/bin/sh
# Assemble the GitHub Pages demo in build/pages: the static files in pages/
# plus the recorded H1 equity curves (make research ARGS=equity-curves) and
# their statistical assessment (make stats) and, when present, the market-structure
# diagnostics (physics.json).
set -eu

ROOT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
OUT="$ROOT_DIR/build/pages"

rm -rf "$OUT"
mkdir -p "$OUT/data"
cp "$ROOT_DIR/pages/index.html" "$OUT/"
cp -R "$ROOT_DIR/pages/assets" "$OUT/"
rm -rf "$OUT/assets/screenshots"
cp "$ROOT_DIR/research/experiments/H1/equity-curves.json" "$OUT/data/"
cp "$ROOT_DIR/research/experiments/H1/statistics.json" "$OUT/data/"
if [ -f "$ROOT_DIR/research/experiments/H1/physics.json" ]; then
  cp "$ROOT_DIR/research/experiments/H1/physics.json" "$OUT/data/"
else
  echo "notice: research/experiments/H1/physics.json not found; the page will show it as not loaded" >&2
fi
PYTHONPATH="$ROOT_DIR/src" python3 -m sq.research.desk --out-dir "$OUT/desk" >/dev/null
echo "built $OUT"
