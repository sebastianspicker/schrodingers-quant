"""Validate with the pinned Freqtrade runtime, without contacting an exchange."""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from freqtrade.enums import RunMode

# Freqtrade is imported inside the functions that need it, so the pure checks
# below (`check_tracked_invariants`, `check_merged_secrets`, the layer checks) are
# importable and unit-testable on a development machine without the pinned image.

TRACKED_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
TRACKED_BASE_CONFIG = TRACKED_CONFIG_DIR / "base.json"
TRACKED_LIVE_CONFIG = TRACKED_CONFIG_DIR / "live.json"
TRACKED_EXAMPLES_DIR = TRACKED_CONFIG_DIR / "examples"
# The tracked config directory and its read-only mount inside the containers.
CONTAINER_CONFIG_DIR = Path("/freqtrade/config")
TRACKED_CONFIG_DIRS = (TRACKED_CONFIG_DIR, CONTAINER_CONFIG_DIR)

PLACEHOLDER_JWT_SECRET_KEY = "REPLACE_WITH_RANDOM_32_CHARS_KEY"
MIN_JWT_SECRET_KEY_LENGTH = 32

# Every field that holds a credential. `telegram.chat_id` is not secret on its
# own, but it identifies the operator's chat and pairs with the bot token, so
# tracked files must leave it empty too. `api_server.username` is not listed:
# it is an account name, not a credential.
CREDENTIAL_FIELDS = (
    ("exchange", "key"),
    ("exchange", "secret"),
    ("exchange", "password"),
    ("exchange", "uid"),
    ("telegram", "token"),
    ("telegram", "chat_id"),
    ("api_server", "password"),
    ("api_server", "jwt_secret_key"),
    ("api_server", "ws_token"),
)
# Freqtrade passes these exchange sub-objects straight to ccxt, which also
# accepts credentials there; tracked files must not set them either.
CCXT_CONFIG_BLOCKS = ("ccxt_config", "ccxt_async_config", "ccxt_sync_config")
# ccxt's requiredCredentials names (walletAddress identifies the account, like chat_id).
CCXT_CREDENTIAL_FIELDS = (
    "apiKey",
    "secret",
    "password",
    "uid",
    "login",
    "privateKey",
    "walletAddress",
    "token",
    "twofa",
)
PLACEHOLDER_PREFIXES = ("REPLACE_", "REPLACE-", "CHANGE_ME", "CHANGE-ME", "CHANGEME")


def is_placeholder(value: object) -> bool:
    """True for example placeholders such as `REPLACE_WITH_...`, `CHANGE_ME` or `<token>`."""
    if not isinstance(value, str):
        return False
    text = value.strip()
    return (
        text == PLACEHOLDER_JWT_SECRET_KEY
        or text.upper().startswith(PLACEHOLDER_PREFIXES)
        or (len(text) > 1 and text.startswith("<") and text.endswith(">"))
    )


def is_blank(value: object) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def read_config_file(path: Path | str) -> dict:
    """Read one config layer as raw JSON, without Freqtrade's defaults or merging.

    Raises ValueError naming the file and, for a syntax error, only the line and
    column: the text around the error may be a credential, so it is never shown.
    Every layer is read here before Freqtrade loads it, because Freqtrade's own
    parse error quotes the surrounding config text.
    """
    try:
        text = Path(path).read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise ValueError(f"{path}: not valid UTF-8 text.") from None
    except OSError as exc:
        raise ValueError(f"{path}: cannot read ({exc.strerror or type(exc).__name__}).") from None
    try:
        config = _parse_json(text)
    except ValueError as exc:
        position = _error_position(text, exc)
        where = f" at line {position[0]}, column {position[1]}" if position else ""
        raise ValueError(f"{path}: invalid JSON{where}.") from None
    if not isinstance(config, dict):
        raise ValueError(f"{path}: the top level must be a JSON object.")
    return config


def _parse_json(text: str) -> object:
    # Parse as Freqtrade does (rapidjson with comments and trailing commas) when
    # rapidjson is available, i.e. in the image; plain JSON elsewhere.
    try:
        import rapidjson
    except ImportError:
        return json.loads(text)
    return rapidjson.loads(text, parse_mode=rapidjson.PM_COMMENTS | rapidjson.PM_TRAILING_COMMAS)


def _error_position(text: str, exc: ValueError) -> tuple[int, int] | None:
    if isinstance(exc, json.JSONDecodeError):
        return exc.lineno, exc.colno
    # rapidjson: "Parse error at offset N: ..." (offset into the UTF-8 encoding).
    match = re.search(r"offset (\d+)", str(exc))
    if not match:
        return None
    before = text.encode("utf-8")[: int(match.group(1))].decode("utf-8", errors="ignore")
    return before.count("\n") + 1, len(before) - (before.rfind("\n") + 1) + 1


def _section(config: dict, section: str, name: str = "", *, required: bool = False) -> dict:
    """Return `config[section]`, raising ValueError if it is not a JSON object."""
    prefix = f"{name}: " if name else ""
    if section not in config or config[section] is None:
        if required:
            raise ValueError(f"{prefix}{section} section is missing.")
        return {}
    if not isinstance(config[section], dict):
        raise ValueError(f"{prefix}{section} must be a JSON object.")
    return config[section]


def check_tracked_credentials(config: dict, name: str) -> None:
    """Raise if a tracked config file sets any credential field (placeholders are allowed).

    Names the file and field only; never prints the value.
    """
    for section, field in CREDENTIAL_FIELDS:
        value = _section(config, section, name).get(field)
        if not is_blank(value) and not is_placeholder(value):
            raise ValueError(
                f"{name}: tracked config must not contain credentials ({section}.{field} is set)."
            )
    exchange = _section(config, "exchange", name)
    for block in CCXT_CONFIG_BLOCKS:
        ccxt_config = _section(exchange, block, f"{name}: exchange")
        for field in CCXT_CREDENTIAL_FIELDS:
            value = ccxt_config.get(field)
            if not is_blank(value) and not is_placeholder(value):
                raise ValueError(
                    f"{name}: tracked config must not contain credentials "
                    f"(exchange.{block}.{field} is set)."
                )


def check_layer_invariants(config: dict, name: str, *, live: bool) -> None:
    """Raise if a config layer (or a merged config) weakens a dry-run safety default.

    Only the tracked live overlay (`live=True`) may set `dry_run` to false; every
    layer, the live overlay included, must keep `initial_state` stopped and forced
    entries disabled.
    """
    if not live and "dry_run" in config and config["dry_run"] is not True:
        raise ValueError(f"{name}: dry_run must stay true outside config/live.json.")
    if "initial_state" in config and config["initial_state"] != "stopped":
        raise ValueError(f'{name}: initial_state must stay "stopped".')
    if "force_entry_enable" in config and config["force_entry_enable"] is not False:
        raise ValueError(f"{name}: force_entry_enable must stay false.")


def check_layer_placeholders(config: dict, name: str, *, allow: bool) -> None:
    """Raise if a config layer still holds a placeholder credential, unless `allow`.

    Names the file and field only; never prints the value.
    """
    if allow:
        return
    for section, field in CREDENTIAL_FIELDS:
        if is_placeholder(_section(config, section, name).get(field)):
            raise ValueError(f"{name}: {section}.{field} is still the example placeholder.")


def check_allow_placeholders_scope(paths: list[Path | str]) -> None:
    """Raise unless every layer is tracked: --allow-placeholders is for the examples only."""
    for path in paths:
        if layer_kind(path) == "local":
            raise ValueError(
                f"--allow-placeholders only applies to config/examples/*; {path} is not an example."
            )


def layer_kind(path: Path | str) -> str:
    """Classify a --config path: "base", "live", "example" (all tracked) or "local".

    A layer is tracked only if it lives in the tracked config directory: the
    repository's `config/` or its container mount `/freqtrade/config`. Any
    other location is "local", even if its trailing components look like a
    tracked file (e.g. `config/local/config/live.json`).
    """
    resolved = Path(path).resolve()
    if resolved.parent in TRACKED_CONFIG_DIRS:
        if resolved.name == "base.json":
            return "base"
        if resolved.name == "live.json":
            return "live"
    if resolved.parent.name == "examples" and resolved.parent.parent in TRACKED_CONFIG_DIRS:
        return "example"
    return "local"


def load_config(paths: list[Path | str], run_mode: RunMode | None = None) -> dict:
    """Load and merge Freqtrade config files; Freqtrade merges later files over earlier.

    `run_mode` defaults to `RunMode.DRY_RUN`.

    Pins `user_data_dir` to the image's `/freqtrade/user_data` (the container
    path the configs already use) instead of Freqtrade's default
    `Path.cwd() / "user_data"`: the `tools` service runs from a read-only
    `/workspace`, where Freqtrade's creation of user_data subfolders would
    fail.
    """
    from freqtrade.configuration import Configuration
    from freqtrade.enums import RunMode

    return Configuration(
        {"config": [str(path) for path in paths], "user_data_dir": "/freqtrade/user_data"},
        run_mode if run_mode is not None else RunMode.DRY_RUN,
    ).get_config()


def check_tracked_invariants(config: dict) -> None:
    """Raise if the tracked scaffold config violates a dry-run safety invariant."""
    if config.get("dry_run") is not True or config.get("trading_mode") != "spot":
        raise ValueError("The scaffold must use spot dry-run mode.")
    if config.get("initial_state") != "stopped":
        raise ValueError("The idle scaffold must start stopped.")
    for section, field in CREDENTIAL_FIELDS:
        value = _section(config, section, required=True).get(field)
        if not is_blank(value) and not is_placeholder(value):
            raise ValueError(
                f"The tracked scaffold must not contain credentials ({section}.{field} is set)."
            )
    if _section(config, "api_server", required=True).get("enabled") or _section(
        config, "telegram", required=True
    ).get("enabled"):
        raise ValueError("Control interfaces must remain disabled in the scaffold.")
    if config.get("force_entry_enable"):
        raise ValueError("Forced entries must remain disabled in the scaffold.")


def check_merged_secrets(config: dict, *, allow_placeholders: bool = False) -> None:
    """Raise if a merged/layered config enables a control interface with a weak credential.

    Names the offending field only; never prints its value.
    """
    if allow_placeholders:
        return
    api_server = _section(config, "api_server")
    if api_server.get("enabled"):
        if is_blank(api_server.get("username")):
            raise ValueError("api_server.enabled is true but username is empty.")
        _check_credential(api_server, "api_server", "password")
        jwt_secret_key = api_server.get("jwt_secret_key") or ""
        if is_blank(jwt_secret_key):
            raise ValueError("api_server.enabled is true but jwt_secret_key is empty.")
        if len(jwt_secret_key) < MIN_JWT_SECRET_KEY_LENGTH:
            raise ValueError(
                f"api_server.enabled is true but jwt_secret_key is shorter than "
                f"{MIN_JWT_SECRET_KEY_LENGTH} characters."
            )
        _check_credential(api_server, "api_server", "jwt_secret_key")
        _check_credential(api_server, "api_server", "ws_token")

    telegram = _section(config, "telegram")
    if telegram.get("enabled"):
        _check_credential(telegram, "telegram", "token")
        _check_credential(telegram, "telegram", "chat_id")


def _check_credential(section_config: dict, section: str, field: str) -> None:
    value = section_config.get(field)
    if is_blank(value):
        raise ValueError(f"{section}.enabled is true but {field} is empty.")
    if is_placeholder(value):
        raise ValueError(f"{section}.enabled is true but {field} is still the example placeholder.")


def load_strategy(config: dict):
    """Load and consistency-check the strategy named in config. Raises on failure."""
    # Raise failures directly: some Freqtrade utility commands log errors yet
    # return success. Use trading-mode validation without creating an Exchange.
    from freqtrade.configuration.config_validation import validate_config_consistency
    from freqtrade.resolvers import StrategyResolver

    strategy = StrategyResolver.load_strategy(config)
    validate_config_consistency(config)
    return strategy


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        action="append",
        dest="configs",
        metavar="PATH",
        help=(
            "Full layered config file list, later overrides earlier; may be repeated. "
            "Validated in addition to the tracked base config: each file's safety checks, "
            "then the merged result's."
        ),
    )
    parser.add_argument(
        "--allow-placeholders",
        action="store_true",
        help=(
            "Skip the merged-config secrets check (empty/placeholder credentials) and allow "
            "placeholders in config/examples/* layers. Refused if any other layer than the "
            "tracked base, live and example configs is given."
        ),
    )
    args = parser.parse_args()
    _quiet_freqtrade_schema_log()
    try:
        validate(args.configs or [], allow_placeholders=args.allow_placeholders)
    except (ValueError, OSError, *_freqtrade_errors()) as exc:
        # One line, no traceback. Only the first line of the message: Freqtrade
        # appends config excerpts on later lines.
        message = str(exc).strip().splitlines()
        schema_message = _schema_error_message(exc)
        if schema_message is not None:
            message = [schema_message]
        print(f"sq.config: {message[0] if message else type(exc).__name__}", file=sys.stderr)
        return 1
    return 0


FREQTRADE_SCHEMA_MODULE = "freqtrade.configuration.config_validation"


class _DropSchemaReason(logging.Filter):
    """Drop Freqtrade's "Invalid configuration. Reason: ..." log: it quotes the value."""

    def filter(self, record: logging.LogRecord) -> bool:
        return not str(record.msg).startswith("Invalid configuration")


def _quiet_freqtrade_schema_log() -> None:
    logger = logging.getLogger(FREQTRADE_SCHEMA_MODULE)
    if not any(isinstance(f, _DropSchemaReason) for f in logger.filters):
        logger.addFilter(_DropSchemaReason())


def _schema_error_message(exc: BaseException) -> str | None:
    """A value-free message for a config schema failure, or None if `exc` is not one.

    A jsonschema error's message quotes the offending value, which can be a
    credential. Freqtrade re-raises it as a ConfigurationError carrying that
    message, while handling the original ValidationError: look through the
    exception chain for the field path and failed rule, and fall back to a
    generic line when the error was raised inside Freqtrade's
    `validate_config_schema`.
    """
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        validator = getattr(current, "validator", None)
        if isinstance(validator, str) and hasattr(current, "absolute_path"):
            field = ".".join(str(part) for part in current.absolute_path) or "(top level)"
            return f"config schema check failed at {field} ({validator})"
        current = current.__cause__ or current.__context__
    # Only validate_config_schema quotes values; the consistency helpers in the
    # same module raise constant, actionable messages, which are kept.
    tb = exc.__traceback__
    while tb is not None:
        frame = tb.tb_frame
        if (
            frame.f_globals.get("__name__") == FREQTRADE_SCHEMA_MODULE
            and frame.f_code.co_name == "validate_config_schema"
        ):
            return "config schema check failed (value not shown; check field types)"
        tb = tb.tb_next
    return None


def _freqtrade_errors() -> tuple[type[BaseException], ...]:
    errors: list[type[BaseException]] = []
    try:
        from freqtrade.exceptions import FreqtradeException

        errors.append(FreqtradeException)
    except ImportError:
        pass
    try:
        from jsonschema import ValidationError

        errors.append(ValidationError)
    except ImportError:
        pass
    return tuple(errors)


def _named(name: object, check, *args, **kwargs) -> None:
    try:
        check(*args, **kwargs)
    except ValueError as exc:
        raise ValueError(f"{name}: {exc}") from None


def validate(configs: list[str], *, allow_placeholders: bool = False) -> None:
    """Run every check; raise ValueError (or a Freqtrade error) on the first failure."""
    # Raw per-file checks first, before Freqtrade merges the layers: a merged
    # config can no longer tell which file set a value.
    check_tracked_credentials(read_config_file(TRACKED_BASE_CONFIG), str(TRACKED_BASE_CONFIG))
    live_overlay = read_config_file(TRACKED_LIVE_CONFIG)
    check_tracked_credentials(live_overlay, str(TRACKED_LIVE_CONFIG))
    check_layer_invariants(live_overlay, str(TRACKED_LIVE_CONFIG), live=True)
    for example in sorted(TRACKED_EXAMPLES_DIR.glob("*.json")):
        example_config = read_config_file(example)
        check_tracked_credentials(example_config, str(example))
        check_layer_invariants(example_config, str(example), live=False)
    if allow_placeholders:
        check_allow_placeholders_scope(configs)
    for path in configs:
        kind = layer_kind(path)
        layer = read_config_file(path)
        if kind != "local":
            check_tracked_credentials(layer, str(path))
        check_layer_invariants(layer, str(path), live=kind == "live")
        check_layer_placeholders(layer, str(path), allow=allow_placeholders and kind == "example")

    base_config = load_config([TRACKED_BASE_CONFIG])
    _named(TRACKED_BASE_CONFIG, check_tracked_invariants, base_config)
    strategy = load_strategy(base_config)
    print(f"Validated tracked spot dry-run configuration and {type(strategy).__name__}.")

    if configs:
        merged_config = load_config(configs)
        check_layer_invariants(
            merged_config,
            "merged config",
            live=any(layer_kind(path) == "live" for path in configs),
        )
        _named(
            "merged config",
            check_merged_secrets,
            merged_config,
            allow_placeholders=allow_placeholders,
        )
        merged_strategy = load_strategy(merged_config)
        print(f"Validated layered configuration and {type(merged_strategy).__name__}.")

    print("No exchange connection or order execution was tested.")


if __name__ == "__main__":
    sys.exit(main())
