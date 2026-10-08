"""Tests for the pure checks in `sq.config` and the tracked config files."""

import copy
import json

import pytest
from conftest import REPO_ROOT

from sq.config import (
    MIN_JWT_SECRET_KEY_LENGTH,
    PLACEHOLDER_JWT_SECRET_KEY,
    check_merged_secrets,
    check_tracked_invariants,
)

CONFIG_DIR = REPO_ROOT / "config"


@pytest.fixture
def base() -> dict:
    return json.loads((CONFIG_DIR / "base.json").read_text())


# --- tracked invariants ---------------------------------------------------------


def test_tracked_base_config_passes(base):
    check_tracked_invariants(base)


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("dry_run",), False, "spot dry-run"),
        (("trading_mode",), "futures", "spot dry-run"),
        (("initial_state",), "running", "start stopped"),
        (("exchange", "key"), "k", "credentials"),
        (("exchange", "secret"), "s", "credentials"),
        (("api_server", "enabled"), True, "Control interfaces"),
        (("telegram", "enabled"), True, "Control interfaces"),
        (("force_entry_enable",), True, "Forced entries"),
    ],
)
def test_tracked_invariant_violations_raise(base, path, value, message):
    config = copy.deepcopy(base)
    target = config
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value
    with pytest.raises(ValueError, match=message):
        check_tracked_invariants(config)


def test_tracked_invariants_reject_missing_dry_run(base):
    del base["dry_run"]
    with pytest.raises(ValueError, match="spot dry-run"):
        check_tracked_invariants(base)


# --- merged secrets -----------------------------------------------------------------

GOOD_JWT = "x" * 40
SECRET_VALUES = ("hunter2-password", "tg-token-value", GOOD_JWT)


def merged(**api) -> dict:
    api_server = {
        "enabled": True,
        "username": "user",
        "password": "hunter2-password",
        "jwt_secret_key": GOOD_JWT,
        **api,
    }
    return {"api_server": api_server, "telegram": {"enabled": False}}


def test_valid_merged_config_passes():
    check_merged_secrets(merged())
    check_merged_secrets({})
    check_merged_secrets(
        {"telegram": {"enabled": True, "token": "tg-token-value", "chat_id": "123"}}
    )


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"username": ""}, "username"),
        ({"password": ""}, "password"),
        ({"jwt_secret_key": ""}, "jwt_secret_key"),
        ({"jwt_secret_key": "short-secret"}, "jwt_secret_key"),
        ({"jwt_secret_key": PLACEHOLDER_JWT_SECRET_KEY}, "jwt_secret_key"),
    ],
)
def test_weak_api_credentials_raise_naming_the_field(overrides, field):
    config = merged(**overrides)
    with pytest.raises(ValueError) as excinfo:
        check_merged_secrets(config)
    message = str(excinfo.value)
    assert field in message
    for value in (*SECRET_VALUES, overrides.get("jwt_secret_key", "")):
        if value:
            assert value not in message


def test_placeholder_is_long_enough_so_it_is_caught_by_its_own_check():
    assert len(PLACEHOLDER_JWT_SECRET_KEY) >= MIN_JWT_SECRET_KEY_LENGTH
    with pytest.raises(ValueError, match="placeholder"):
        check_merged_secrets(merged(jwt_secret_key=PLACEHOLDER_JWT_SECRET_KEY))


@pytest.mark.parametrize(
    ("telegram", "field"),
    [
        ({"enabled": True, "token": "", "chat_id": "1"}, "token"),
        ({"enabled": True, "token": "t", "chat_id": ""}, "chat_id"),
        ({"enabled": True}, "token"),
    ],
)
def test_telegram_enabled_without_credentials_raises(telegram, field):
    with pytest.raises(ValueError, match=field):
        check_merged_secrets({"telegram": telegram})


def test_allow_placeholders_skips_checks():
    check_merged_secrets(merged(jwt_secret_key="", username=""), allow_placeholders=True)
    check_merged_secrets({"telegram": {"enabled": True}}, allow_placeholders=True)


def test_disabled_interfaces_are_not_checked():
    check_merged_secrets(
        {"api_server": {"enabled": False, "username": ""}, "telegram": {"enabled": False}}
    )


# --- tracked live overlay ---------------------------------------------------------------


def test_live_overlay_has_no_credentials_and_is_stopped():
    live = json.loads((CONFIG_DIR / "live.json").read_text())
    assert "exchange" not in live
    assert "key" not in live and "secret" not in live
    assert live["dry_run"] is False
    assert live["initial_state"] == "stopped"
    assert live["force_entry_enable"] is False
