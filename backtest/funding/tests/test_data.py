"""Data pagination/cache checks use fake HTTP only."""
import pandas as pd

from backtest.funding import data


class Response:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


class Session:
    def __init__(self, pages):
        self.pages = iter(pages)
        self.calls = []

    def get(self, url, params, timeout):
        self.calls.append((url, params.copy()))
        return Response(next(self.pages))


def test_funding_pagination_and_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(data, "CACHE_DIR", tmp_path)
    lo = int(pd.Timestamp("2023-01-01", tz="UTC").timestamp() * 1000)
    client = Session([[dict(fundingTime=lo, fundingRate=".001", markPrice="100")],
                      [dict(fundingTime=lo + 8 * 3600_000, fundingRate="-.001", markPrice="101")], []])
    frame = data.download_funding("BTCUSDT", "2023-01-01", "2023-01-02", session=client)
    assert len(frame) == 2
    assert client.calls[1][1]["startTime"] == lo + 1
    assert client.calls[2][1]["startTime"] == lo + 8 * 3600_000 + 1
    cached = data.download_funding("BTCUSDT", "2023-01-01", "2023-01-02", session=Session([]))
    pd.testing.assert_frame_equal(frame, cached)


def test_klines_pagination_excludes_incomplete_bar(tmp_path, monkeypatch):
    monkeypatch.setattr(data, "CACHE_DIR", tmp_path)
    lo = int(pd.Timestamp("2023-01-01", tz="UTC").timestamp() * 1000)
    step = 8 * 3600_000

    def candle(t):
        return [t, "100", "101", "99", "100", "10", t + step - 1]

    client = Session([[candle(lo)], [candle(lo + step), candle(lo + 2 * step)]])
    result = data.download_klines("BTCUSDT", "2023-01-01", "2023-01-01T20:00Z", session=client)
    assert len(result) == 2
    assert client.calls[1][1]["startTime"] == lo + step
    assert result.close.tolist() == [100., 100.]


def test_empty_funding_cached(tmp_path, monkeypatch):
    monkeypatch.setattr(data, "CACHE_DIR", tmp_path)
    frame = data.download_funding("BTCUSDT", "2023-01-01", "2023-01-02", session=Session([[]]))
    cached = data.download_funding("BTCUSDT", "2023-01-01", "2023-01-02", session=Session([]))
    assert frame.empty and cached.empty


def test_alignment_fills_each_market_independently():
    import pytest
    from backtest.funding.tests.test_engine import candles

    full = candles(6)
    full['volume'] = 10.0
    spot = full.drop(full.index[2])
    perp = full.drop(full.index[3])
    with pytest.warns(UserWarning, match="forward-filled") as notices:
        s, p = data.align_candles(spot, perp, 8)
    assert len(notices) == 2
    assert s.index.equals(p.index)
    assert len(s) == 6
    assert s.loc[full.index[2], 'volume'] == 0
    assert p.loc[full.index[2], 'volume'] == 10
    assert p.loc[full.index[3], 'close'] == full.iloc[2].close


def test_alignment_rejects_only_gaps_over_limit():
    import pytest
    from backtest.funding.tests.test_engine import candles

    full = candles(28)
    with pytest.warns(UserWarning):
        s, p = data.align_candles(full.drop(full.index[1:25]), full, 8)
    assert len(s) == len(p) == len(full)
    with pytest.raises(ValueError, match="exceeds 24"):
        data.align_candles(full.drop(full.index[1:26]), full, 8)


def test_alignment_trims_to_common_coverage():
    from backtest.funding.tests.test_engine import candles

    full = candles()
    s, p = data.align_candles(full.iloc[:-1], full.iloc[1:], 8)
    assert s.index.equals(full.index[1:-1])
    assert s.index.equals(p.index)
