"""Contract tests for the tracked base config and the layered config validator."""

import json

import pytest
from conftest import build_config

from sq import config


def test_tracked_base_config_satisfies_invariants():
    loaded = config.load_config([config.TRACKED_BASE_CONFIG])
    config.check_tracked_invariants(loaded)


def test_tracked_base_strategy_loads():
    loaded = config.load_config([config.TRACKED_BASE_CONFIG])
    strategy = config.load_strategy(loaded)
    assert type(strategy).__name__ == loaded["strategy"]


def test_tracked_base_db_url_is_dry_run_sqlite(repo_root):
    base_config = json.loads((repo_root / "config" / "base.json").read_text())
    assert base_config["db_url"] == "sqlite:////freqtrade/user_data/runtime/dry-run.sqlite"


def test_running_initial_state_is_rejected(tmp_path):
    overlay = tmp_path / "running.json"
    overlay.write_text(json.dumps({"initial_state": "running"}))

    loaded = build_config([config.TRACKED_BASE_CONFIG, overlay])

    with pytest.raises(ValueError, match="must start stopped"):
        config.check_tracked_invariants(loaded)


def test_non_empty_exchange_key_is_rejected(tmp_path):
    overlay = tmp_path / "credentials.json"
    overlay.write_text(json.dumps({"exchange": {"key": "dummy-key"}}))

    loaded = build_config([config.TRACKED_BASE_CONFIG, overlay])

    with pytest.raises(ValueError, match="must not contain exchange credentials"):
        config.check_tracked_invariants(loaded)


def _api_server_overlay(tmp_path, **api_server_overrides):
    api_server = {
        "enabled": True,
        "username": "operator",
        "password": "correct-horse-battery-staple",
        "jwt_secret_key": "a" * 32,
    }
    api_server.update(api_server_overrides)
    overlay = tmp_path / "api-server.json"
    overlay.write_text(json.dumps({"api_server": api_server}))
    return overlay


def _telegram_overlay(tmp_path, **telegram_overrides):
    telegram = {"enabled": True, "token": "bot-token", "chat_id": "12345"}
    telegram.update(telegram_overrides)
    overlay = tmp_path / "telegram.json"
    overlay.write_text(json.dumps({"telegram": telegram}))
    return overlay


def test_merged_secrets_rejects_empty_api_server_username(tmp_path):
    overlay = _api_server_overlay(tmp_path, username="")
    loaded = build_config([config.TRACKED_BASE_CONFIG, overlay])

    with pytest.raises(ValueError, match="username is empty"):
        config.check_merged_secrets(loaded)


def test_merged_secrets_rejects_empty_api_server_password(tmp_path):
    overlay = _api_server_overlay(tmp_path, password="")
    loaded = build_config([config.TRACKED_BASE_CONFIG, overlay])

    with pytest.raises(ValueError, match="password is empty"):
        config.check_merged_secrets(loaded)


def test_merged_secrets_rejects_empty_jwt_secret_key(tmp_path):
    overlay = _api_server_overlay(tmp_path, jwt_secret_key="")
    loaded = build_config([config.TRACKED_BASE_CONFIG, overlay])

    with pytest.raises(ValueError, match="jwt_secret_key is empty"):
        config.check_merged_secrets(loaded)


def test_merged_secrets_rejects_short_jwt_secret_key(tmp_path):
    overlay = _api_server_overlay(tmp_path, jwt_secret_key="short")
    loaded = build_config([config.TRACKED_BASE_CONFIG, overlay])

    with pytest.raises(ValueError, match="shorter than 32 characters"):
        config.check_merged_secrets(loaded)


def test_merged_secrets_rejects_placeholder_jwt_secret_key(tmp_path):
    overlay = _api_server_overlay(tmp_path, jwt_secret_key="REPLACE_WITH_RANDOM_32_CHARS_KEY")
    loaded = build_config([config.TRACKED_BASE_CONFIG, overlay])

    with pytest.raises(ValueError, match="example placeholder"):
        config.check_merged_secrets(loaded)


def test_merged_secrets_rejects_empty_telegram_token(tmp_path):
    overlay = _telegram_overlay(tmp_path, token="")
    loaded = build_config([config.TRACKED_BASE_CONFIG, overlay])

    with pytest.raises(ValueError, match="token is empty"):
        config.check_merged_secrets(loaded)


def test_merged_secrets_rejects_empty_telegram_chat_id(tmp_path):
    overlay = _telegram_overlay(tmp_path, chat_id="")
    loaded = build_config([config.TRACKED_BASE_CONFIG, overlay])

    with pytest.raises(ValueError, match="chat_id is empty"):
        config.check_merged_secrets(loaded)


def test_merged_secrets_passes_with_real_credentials(tmp_path):
    overlay_api = _api_server_overlay(tmp_path)
    overlay_telegram = _telegram_overlay(tmp_path)
    loaded = build_config([config.TRACKED_BASE_CONFIG, overlay_api, overlay_telegram])

    config.check_merged_secrets(loaded)


def test_merged_secrets_allow_placeholders_skips_the_check(tmp_path):
    overlay = _api_server_overlay(tmp_path, username="", password="", jwt_secret_key="")
    loaded = build_config([config.TRACKED_BASE_CONFIG, overlay])

    config.check_merged_secrets(loaded, allow_placeholders=True)


def test_local_overlay_merges_and_passes_layered_validation(tmp_path):
    overlay = tmp_path / "secrets.json"
    overlay.write_text(json.dumps({"exchange": {"key": "dummy-key", "secret": "dummy-secret"}}))

    # The layered validation accepts credentials in a local overlay: it does not
    # enforce the tracked-only invariants on the merged result.
    merged_config = config.load_config([config.TRACKED_BASE_CONFIG, overlay])
    assert merged_config["exchange"]["key"] == "dummy-key"
    strategy = config.load_strategy(merged_config)
    assert type(strategy).__name__ == merged_config["strategy"]

    # The tracked-only check on base.json alone still passes, unaffected by the overlay.
    base_config = config.load_config([config.TRACKED_BASE_CONFIG])
    config.check_tracked_invariants(base_config)
