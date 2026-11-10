from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from conftest import make_frame
from usquant.data import (
    CSVProvider,
    DataProvider,
    DataStore,
    DataValidationError,
    MarketData,
    SyntheticProvider,
    YahooProvider,
    adjust_ohlc,
    make_provider,
    validate_ohlcv,
)


def test_validate_sorts_dedupes_and_types():
    df = make_frame([10, 11, 12])
    shuffled = pd.concat([df.iloc[[2, 0, 1]], df.iloc[[1]]])
    out = validate_ohlcv(shuffled, "X")
    assert out.index.is_monotonic_increasing
    assert out.index.is_unique
    assert out.index.name == "date"
    assert len(out) == 3


def test_validate_rejects_missing_columns_and_bad_prices():
    df = make_frame([10, 11, 12])
    with pytest.raises(DataValidationError):
        validate_ohlcv(df.drop(columns=["adj_close"]), "X")
    bad = df.copy()
    bad.iloc[1, bad.columns.get_loc("close")] = -1.0
    with pytest.raises(DataValidationError):
        validate_ohlcv(bad, "X")
    inverted = df.copy()
    inverted["high"], inverted["low"] = df["low"], df["high"]
    with pytest.raises(DataValidationError):
        validate_ohlcv(inverted, "X")


def test_adjust_ohlc_applies_adj_factor_to_all_prices():
    # A 2-for-1 split on day 3: raw prices halve, adj_close is continuous.
    raw_close = np.array([100.0, 102.0, 51.0, 52.0])
    adj_factor = np.array([0.5, 0.5, 1.0, 1.0])
    df = make_frame(raw_close, open_=raw_close, adj_factor=adj_factor)
    adj = adjust_ohlc(df)
    np.testing.assert_allclose(adj["close"], [50.0, 51.0, 51.0, 52.0])
    np.testing.assert_allclose(adj["open"], adj["close"])
    # Adjusted returns show no artificial -50% split "crash".
    assert adj["close"].pct_change().min() > -0.01


def test_market_data_aligns_on_common_dates():
    a = make_frame([1, 2, 3, 4, 5])
    b = make_frame([1, 2, 3, 4, 5]).iloc[1:]
    md = MarketData.from_frames({"A": a, "B": b})
    assert len(md.dates) == 4
    assert md.symbols == ["A", "B"]
    assert not md.close.isna().any().any()


def test_synthetic_provider_is_deterministic_and_slice_consistent():
    p = SyntheticProvider(seed=1)
    full = p.fetch("SPY", "2010-01-01", "2020-12-31")
    again = SyntheticProvider(seed=1).fetch("SPY", "2010-01-01", "2020-12-31")
    pd.testing.assert_frame_equal(full, again)
    sub = p.fetch("SPY", "2015-01-01", "2016-12-31")
    pd.testing.assert_frame_equal(sub, full.loc["2015-01-01":"2016-12-31"])
    other_seed = SyntheticProvider(seed=2).fetch("SPY", "2010-01-01", "2020-12-31")
    assert not np.allclose(full["close"], other_seed["close"])
    assert not np.allclose(full["close"].to_numpy(), p.fetch("QQQ", "2010-01-01", "2020-12-31")["close"].to_numpy())


def test_csv_provider_roundtrip(tmp_path):
    df = make_frame([10, 11, 12, 13])
    out = df.rename(columns={"adj_close": "Adj Close", "close": "Close"})
    out.to_csv(tmp_path / "ABC.csv", index_label="Date")
    loaded = CSVProvider(tmp_path).fetch("ABC")
    np.testing.assert_allclose(loaded["close"], df["close"])
    np.testing.assert_allclose(loaded["adj_close"], df["adj_close"])
    assert make_provider("csv", directory=tmp_path).fetch("ABC", "2024-01-02").shape[0] == 3


class CountingProvider(DataProvider):
    name = "counting"

    def __init__(self):
        self.calls = 0
        self.inner = SyntheticProvider(seed=3)

    def fetch(self, symbol, start=None, end=None):
        self.calls += 1
        return self.inner.fetch(symbol, start, end)


def test_datastore_caches_and_refetches_when_range_not_covered(tmp_path):
    store, prov = DataStore(tmp_path), CountingProvider()
    a = store.load(prov, "SPY", "2018-01-01", "2019-12-31")
    b = store.load(prov, "SPY", "2018-06-01", "2019-06-30")
    assert prov.calls == 1  # served from cache
    assert store.path(prov, "SPY").exists()
    np.testing.assert_allclose(b["close"], a.loc["2018-06-01":"2019-06-30", "close"], rtol=1e-6)
    store.load(prov, "SPY", "2017-01-01", "2019-12-31")
    assert prov.calls == 2  # earlier start forces a refetch
    store.load(prov, "SPY", "2017-01-01", "2019-12-31", refresh=True)
    assert prov.calls == 3


def test_yahoo_provider_normalizes_yfinance_output(monkeypatch):
    yf = pytest.importorskip("yfinance")
    raw = make_frame([100.0, 101.0, 102.0]).rename(
        columns={"open": "Open", "high": "High", "low": "Low", "close": "Close", "adj_close": "Adj Close", "volume": "Volume"}
    )
    captured = {}

    def fake_download(symbol, **kwargs):
        captured.update(kwargs, symbol=symbol)
        return raw

    monkeypatch.setattr(yf, "download", fake_download)
    out = YahooProvider().fetch("SPY", "2024-01-01", "2024-01-03")
    assert list(out.columns) == ["open", "high", "low", "close", "adj_close", "volume"]
    assert captured["auto_adjust"] is False
    assert captured["end"] == "2024-01-04"  # inclusive end -> yfinance exclusive end
    assert len(out) == 3
