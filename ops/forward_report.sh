#!/bin/sh
# Serialized daily reports, atomic publication, persistent STOP and window contract.
set -eu
ROOT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
exec python3 "$ROOT_DIR/ops/forward_record.py" "$@"
