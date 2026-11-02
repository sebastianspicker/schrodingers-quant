"""Read-only live-readiness tooling: preflight feasibility and reconciliation.

Never places, cancels or edits an order; see `sq.live.preflight` and
`sq.live.reconcile`.
"""

import argparse
import sqlite3
import sys
from pathlib import Path
from urllib.parse import quote


class CliArgumentParser(argparse.ArgumentParser):
    """argparse, but a usage error exits 1 ("error"), not argparse's 2.

    preflight documents exit 2 as "infeasible"; a mistyped flag must not look
    like a feasibility verdict, and reconcile/forward document only 0, their
    verdict code and 1.
    """

    def error(self, message: str):
        self.print_usage(sys.stderr)
        self.exit(1, f"{self.prog}: error: {message}\n")


class SafeMessageError(ValueError):
    """A deliberate operator-facing error whose message carries no config paths,
    account data or secrets, so the CLIs print it verbatim."""


def cli_error(exc: Exception, tool: str) -> str:
    """The text a live CLI prints for an error that escaped `main`.

    Never print str(exc) of an unexpected exception: it can echo config paths,
    pair/account details or, depending on the failure, credential-adjacent
    text from ccxt. Only deliberate `SafeMessageError`s show their message;
    anything else is named by its exception class only.
    """
    if isinstance(exc, SafeMessageError):
        return str(exc)
    return f"{exc.__class__.__name__}: {tool} failed"


def trade_db_error(exc: sqlite3.Error, db_path: str) -> SafeMessageError:
    """A printable explanation of why Freqtrade's trade database could not be read.

    Distinguishes a database that does not exist yet (it is created by the
    bot's first dry or live run), one the bot holds locked, and one whose
    schema (or file format) the readers do not recognize. The path is not
    echoed.
    """
    if not Path(db_path).exists():
        return SafeMessageError(
            "trade database not found; it is created by a prior dry or live run of the bot "
            "(check db_url in the merged config)"
        )
    if "locked" in str(exc):
        return SafeMessageError("trade database is locked; rerun when the bot is not writing")
    return SafeMessageError(
        "trade database is not a SQLite database or is incompatible with the pinned "
        "Freqtrade schema"
    )


def db_path_from_url(db_url: str) -> str:
    """Filesystem path of a `sqlite:///...` db_url (the readers support SQLite only)."""
    prefix = "sqlite:///"
    if not db_url.startswith(prefix):
        # Never echo the URL: a non-sqlite db_url can carry a password.
        raise SafeMessageError("Only sqlite db_url values are supported")
    if len(db_url) == len(prefix):
        raise SafeMessageError("db_url has no path")
    return db_url[len(prefix) :]


def read_only_uri(db_path: str) -> str:
    """sqlite URI that opens `db_path` read-only. The path is percent-encoded
    so that `?`, `#` or `%` in a file name cannot swallow `mode=ro`."""
    return f"file:{quote(db_path)}?mode=ro"
