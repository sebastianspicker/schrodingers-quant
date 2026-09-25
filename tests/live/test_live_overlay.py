"""Contract tests for the tracked live config overlay.

config/live.json is only ever applied explicitly as a layer on top of
config/base.json; it must never appear in the default validation or trading
path, and it must never carry credentials.
"""

import json
import re
from pathlib import Path

from sq import config

REPO_ROOT = Path(__file__).resolve().parents[2]

LIVE_CONFIG = REPO_ROOT / "config" / "live.json"


def test_base_plus_live_overlay_enables_live_settings():
    merged = config.load_config([config.TRACKED_BASE_CONFIG, LIVE_CONFIG])

    assert merged["dry_run"] is False
    assert merged["initial_state"] == "stopped"
    assert merged["order_types"]["stoploss_on_exchange"] is True
    assert merged["available_capital"] == 10
    assert merged["db_url"] != config.load_config([config.TRACKED_BASE_CONFIG])["db_url"]
    assert merged["db_url"] == "sqlite:////freqtrade/user_data/runtime/live.sqlite"


def test_tracked_base_config_alone_is_still_dry_run():
    # The live overlay must not leak into or be required by the tracked
    # dry-run default: base.json alone still satisfies every invariant.
    loaded = config.load_config([config.TRACKED_BASE_CONFIG])
    config.check_tracked_invariants(loaded)


def makefile_recipe(makefile: str, target: str) -> str:
    """Return the recipe lines of one Makefile target."""
    match = re.search(rf"^{re.escape(target)}:.*\n((?:\t.*\n?)*)", makefile, re.MULTILINE)
    assert match, f"Makefile has no target {target!r}"
    return match.group(1)


def test_default_paths_never_include_live_config(repo_root):
    makefile = (repo_root / "Makefile").read_text()
    compose = (repo_root / "compose.yaml").read_text()

    # Only the explicit validate-live and reconcile targets may name the overlay.
    for target in ("validate", "validate-local", "up", "test"):
        assert "live.json" not in makefile_recipe(makefile, target)
    assert "live.json" not in compose


def test_live_config_has_no_credential_fields():
    live_config = json.loads(LIVE_CONFIG.read_text())

    assert "exchange" not in live_config
    assert "strategy" not in live_config

    forbidden_substrings = ("key", "secret", "token", "password")
    serialized = json.dumps(live_config).lower()
    for substring in forbidden_substrings:
        assert substring not in serialized, f"live.json must not contain a '{substring}' field"
