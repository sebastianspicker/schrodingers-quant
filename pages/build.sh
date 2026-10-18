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
# A malformed copy would only show up in the browser as "not loaded".
for f in "$OUT"/data/*.json; do
  if ! python3 -m json.tool "$f" >/dev/null 2>&1; then
    echo "pages/build.sh: $(basename "$f") is not valid JSON" >&2
    exit 1
  fi
done
# The desk needs Python 3.11+. Prefer uv's interpreter: macOS's python3 is 3.9.
if command -v uv >/dev/null 2>&1; then
  PYTHONPATH="$ROOT_DIR/src" uv run --locked --project "$ROOT_DIR" \
    python -m sq.research.desk --out-dir "$OUT/desk" >/dev/null
elif python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
  PYTHONPATH="$ROOT_DIR/src" python3 -m sq.research.desk --out-dir "$OUT/desk" >/dev/null
else
  echo "pages/build.sh: the desk report needs Python 3.11+ or uv (see README Quick start)" >&2
  exit 1
fi
echo "built $OUT"
