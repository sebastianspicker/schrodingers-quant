import sqlite3

import pandas as pd
import pytest
from conftest import make_candles

from sq.live import candles

STEP = pd.Timedelta(hours=4)


def test_archive_retains_history_and_merges_idempotently(tmp_path):
    path = tmp_path / "market.sqlite"
    history = make_candles([100.0] * 800)
    candles.archive_update(path, history.iloc[:720], "BTC/EUR", "4h", STEP)
    out = candles.archive_update(path, history.iloc[80:], "BTC/EUR", "4h", STEP)
    assert len(out) == 800
    again = candles.archive_update(path, history.iloc[80:], "BTC/EUR", "4h", STEP)
    pd.testing.assert_frame_equal(out, again)
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_revision_or_gap_rolls_back_archive(tmp_path):
    path = tmp_path / "market.sqlite"
    history = make_candles([100.0] * 8)
    candles.archive_update(path, history.iloc[:4], "BTC/EUR", "4h", STEP)
    changed = history.iloc[2:].copy()
    changed.loc[2, "volume"] = 2
    with pytest.raises(candles.CandleError, match="changed"):
        candles.archive_update(path, changed, "BTC/EUR", "4h", STEP)
    with pytest.raises(candles.CandleError, match="gaps"):
        candles.archive_update(path, history.iloc[5:], "BTC/EUR", "4h", STEP)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM candles").fetchone()[0] == 4


@pytest.mark.parametrize(
    "column,value",
    [
        ("close", float("nan")),
        ("open", 0),
        ("high", 90),
        ("low", 110),
        ("volume", -1),
        ("volume", float("inf")),
    ],
)
def test_invalid_prices_fail(column, value):
    frame = make_candles([100.0] * 3)
    frame.loc[1, column] = value
    with pytest.raises(candles.CandleError):
        candles.validate(frame, STEP)


def test_bad_timestamp_grid_and_duplicates_fail():
    frame = make_candles([100.0] * 3)
    for bad in (frame.iloc[::-1], pd.concat([frame, frame.iloc[-1:]]), frame.iloc[[0, 2]]):
        with pytest.raises(candles.CandleError):
            candles.validate(bad, STEP)
    frame.loc[1, "date"] += pd.Timedelta(minutes=1)
    with pytest.raises(candles.CandleError, match="aligned"):
        candles.validate(frame, STEP)


def test_freshness_allows_one_publication_interval():
    frame = make_candles([100.0] * 3)
    end = frame["date"].iloc[-1] + STEP
    candles.require_fresh(frame, STEP, end + STEP)
    with pytest.raises(candles.CandleError, match="stale"):
        candles.require_fresh(frame, STEP, end + STEP + pd.Timedelta(seconds=1))
