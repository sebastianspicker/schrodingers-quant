"""Validate with the pinned Freqtrade runtime, without contacting an exchange."""

import argparse
from pathlib import Path

from freqtrade.configuration import Configuration
from freqtrade.configuration.config_validation import validate_config_consistency
from freqtrade.enums import RunMode
from freqtrade.resolvers import StrategyResolver

from sq.paths import TRACKED_BASE_CONFIG

PLACEHOLDER_JWT_SECRET_KEY = "REPLACE_WITH_RANDOM_32_CHARS_KEY"
MIN_JWT_SECRET_KEY_LENGTH = 32


def load_config(paths: list[Path | str], run_mode: RunMode = RunMode.DRY_RUN) -> dict:
    """Load and merge Freqtrade config files; Freqtrade merges later files over earlier.

    Pins `user_data_dir` to the image's `/freqtrade/user_data` (the container
    path the configs already use) instead of Freqtrade's default
    `Path.cwd() / "user_data"`: the `tools` and `test` services run from a
    read-only `/workspace`, where Freqtrade's creation of user_data subfolders
    would fail.
    """
    return Configuration(
        {"config": [str(path) for path in paths], "user_data_dir": "/freqtrade/user_data"},
        run_mode,
    ).get_config()


def check_tracked_invariants(config: dict) -> None:
    """Raise if the tracked scaffold config violates a dry-run safety invariant."""
    if config.get("dry_run") is not True or config.get("trading_mode") != "spot":
        raise ValueError("The scaffold must use spot dry-run mode.")
    if config.get("initial_state") != "stopped":
        raise ValueError("The idle scaffold must start stopped.")
    if config["exchange"].get("key") or config["exchange"].get("secret"):
        raise ValueError("The tracked scaffold must not contain exchange credentials.")
    if config["api_server"]["enabled"] or config["telegram"]["enabled"]:
        raise ValueError("Control interfaces must remain disabled in the scaffold.")
    if config.get("force_entry_enable"):
        raise ValueError("Forced entries must remain disabled in the scaffold.")


def check_merged_secrets(config: dict, *, allow_placeholders: bool = False) -> None:
    """Raise if a merged/layered config enables a control interface with a weak credential.

    Names the offending field only; never prints its value.
    """
    if allow_placeholders:
        return
    api_server = config.get("api_server", {})
    if api_server.get("enabled"):
        if not api_server.get("username"):
            raise ValueError("api_server.enabled is true but username is empty.")
        if not api_server.get("password"):
            raise ValueError("api_server.enabled is true but password is empty.")
        jwt_secret_key = api_server.get("jwt_secret_key") or ""
        if not jwt_secret_key:
            raise ValueError("api_server.enabled is true but jwt_secret_key is empty.")
        if len(jwt_secret_key) < MIN_JWT_SECRET_KEY_LENGTH:
            raise ValueError(
                f"api_server.enabled is true but jwt_secret_key is shorter than "
                f"{MIN_JWT_SECRET_KEY_LENGTH} characters."
            )
        if jwt_secret_key == PLACEHOLDER_JWT_SECRET_KEY:
            raise ValueError(
                "api_server.enabled is true but jwt_secret_key is still the example placeholder."
            )

    telegram = config.get("telegram", {})
    if telegram.get("enabled"):
        if not telegram.get("token"):
            raise ValueError("telegram.enabled is true but token is empty.")
        if not telegram.get("chat_id"):
            raise ValueError("telegram.enabled is true but chat_id is empty.")


def load_strategy(config: dict):
    """Load and consistency-check the strategy named in config. Raises on failure."""
    # Raise failures directly: some Freqtrade utility commands log errors yet
    # return success. Use trading-mode validation without creating an Exchange.
    strategy = StrategyResolver.load_strategy(config)
    validate_config_consistency(config)
    return strategy


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        action="append",
        dest="configs",
        metavar="PATH",
        help=(
            "Full layered config file list, later overrides earlier; may be repeated. "
            "Validated in addition to the tracked base config, without its invariant checks."
        ),
    )
    parser.add_argument(
        "--allow-placeholders",
        action="store_true",
        help=(
            "Skip the merged-config secrets check (empty/placeholder credentials). "
            "Only for validating example configs (e.g. config/examples/secrets.example.json) "
            "in CI, never for a real layered config."
        ),
    )
    args = parser.parse_args()

    base_config = load_config([TRACKED_BASE_CONFIG])
    check_tracked_invariants(base_config)
    strategy = load_strategy(base_config)
    print(f"Validated tracked spot dry-run configuration and {type(strategy).__name__}.")

    if args.configs:
        merged_config = load_config(args.configs)
        check_merged_secrets(merged_config, allow_placeholders=args.allow_placeholders)
        merged_strategy = load_strategy(merged_config)
        print(f"Validated layered configuration and {type(merged_strategy).__name__}.")

    print("No exchange connection or order execution was tested.")


if __name__ == "__main__":
    main()
