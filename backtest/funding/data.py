"""Public Binance data, cached by exact requested UTC interval [start, end).

No requests are made on import. Funding pagination advances by fundingTime + 1ms;
candles advance by openTime + interval. Only complete candles are returned.
"""
from pathlib import Path
import time

import pandas as pd
import requests

CACHE_DIR = Path(__file__).with_name("data")
SPOT_URL = "https://api.binance.com/api/v3/klines"
PERP_URL = "https://fapi.binance.com/fapi/v1/klines"
FUNDING_URL = "https://fapi.binance.com/fapi/v1/fundingRate"


def utc(value):
    stamp = pd.Timestamp(value)
    return stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")


def _bounds(start, end):
    start, end = utc(start), utc(end)
    if end <= start:
        raise ValueError("end must be after start")
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def _get(session, url, params):
    for attempt in range(5):
        response = session.get(url, params=params, timeout=30)
        if response.status_code in (418, 429) or response.status_code >= 500:
            if attempt < 4:
                time.sleep(min(float(response.headers.get("Retry-After", 2 ** attempt)), 60))
                continue
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise ValueError(f"Unexpected Binance response: {payload}")
        return payload
    raise RuntimeError("Binance retries exhausted")


def _path(kind, symbol, start, end, interval=""):
    if not symbol.isalnum():
        raise ValueError("symbol must contain only letters and digits")
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"{kind}_{symbol.upper()}_{interval}_{start}_{end}.csv"


def _read(path):
    frame = pd.read_csv(path, parse_dates=["timestamp"])
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
    return frame.set_index("timestamp")


def download_funding(symbol, start, end, *, refresh=False, session=None):
    """Return timestamp-indexed funding_rate and mark_price (public endpoint)."""
    lo, hi = _bounds(start, end)
    path = _path("funding", symbol, lo, hi)
    if path.exists() and not refresh:
        return _read(path)
    client = session or requests.Session()
    rows, cursor = [], lo
    try:
        while cursor < hi:
            page = _get(client, FUNDING_URL, dict(symbol=symbol.upper(), startTime=cursor,
                                                 endTime=hi - 1, limit=1000))
            if not page:
                break
            rows.extend(page)
            next_cursor = max(int(r["fundingTime"]) for r in page) + 1
            if next_cursor <= cursor:
                raise ValueError("Funding pagination did not advance")
            cursor = next_cursor
    finally:
        if session is None:
            client.close()
    frame = pd.DataFrame({
        "timestamp": pd.to_datetime([int(r["fundingTime"]) for r in rows], unit="ms", utc=True),
        "funding_rate": [float(r["fundingRate"]) for r in rows],
        "mark_price": [float(r.get("markPrice") or "nan") for r in rows],
    }).set_index("timestamp")
    frame = frame[~frame.index.duplicated()].sort_index()
    frame = frame[(frame.index >= utc(start)) & (frame.index < utc(end))]
    frame.to_csv(path)
    return frame


def download_klines(symbol, start, end, *, market="spot", interval="8h", refresh=False, session=None):
    """Return OHLCV candles indexed by UTC open timestamp."""
    if market not in ("spot", "perp") or interval not in ("1h", "8h"):
        raise ValueError("market must be spot/perp and interval must be 1h/8h")
    lo, hi = _bounds(start, end)
    step = (1 if interval == "1h" else 8) * 3600_000
    complete_before = min(hi, int(pd.Timestamp.now(tz="UTC").timestamp() * 1000))
    path = _path(market, symbol, lo, hi, interval)
    if path.exists() and not refresh:
        return _read(path)
    client = session or requests.Session()
    rows, cursor = [], lo
    try:
        while cursor < hi:
            page = _get(client, SPOT_URL if market == "spot" else PERP_URL,
                        dict(symbol=symbol.upper(), interval=interval, startTime=cursor,
                             endTime=hi - 1, limit=1000))
            if not page:
                break
            rows.extend(r for r in page if int(r[0]) >= lo and int(r[6]) < complete_before)
            next_cursor = max(int(r[0]) for r in page) + step
            if next_cursor <= cursor:
                raise ValueError("Kline pagination did not advance")
            cursor = next_cursor
    finally:
        if session is None:
            client.close()
    frame = pd.DataFrame({"timestamp": pd.to_datetime([int(r[0]) for r in rows], unit="ms", utc=True),
                          **{name: [float(r[i]) for r in rows]
                             for i, name in enumerate(("open", "high", "low", "close", "volume"), 1)}})
    frame = frame.set_index("timestamp")
    frame = frame[~frame.index.duplicated()].sort_index()
    frame.to_csv(path)
    return frame


def align_candles(spot, perp, interval_hours, max_gap=24):
    """Align on the common coverage, filling at most max_gap absent bars per run.

    Synthetic bars use the previous close for all OHLC fields and zero volume.
    No extrapolation is made beyond the overlap of the two markets.
    """
    import warnings

    if spot.empty or perp.empty:
        raise ValueError("Nonempty spot/perp candles required")
    step = pd.Timedelta(hours=interval_hours)
    start, end = max(spot.index[0], perp.index[0]), min(spot.index[-1], perp.index[-1])
    if start > end:
        raise ValueError("No overlapping spot/perp candles")
    grid = pd.date_range(start, end, freq=step)
    aligned = []
    for name, frame in (("spot", spot), ("perp", perp)):
        if ((frame.index - start) % step != pd.Timedelta(0)).any():
            raise ValueError("Irregular candle timestamps")
        missing = ~grid.isin(frame.index)
        runs = pd.Series(missing).groupby(pd.Series(~missing).cumsum()).sum()
        if runs.max() > max_gap:
            raise ValueError(f"Missing {name} candles: gap exceeds {max_gap} bars")
        filled = frame.reindex(grid).copy()
        # Seed from real observations before the overlapping coverage as needed.
        previous = frame.close.reindex(frame.index.union(grid)).ffill().reindex(grid)
        for column in ("open", "high", "low", "close"):
            filled.loc[missing, column] = previous.loc[missing]
        if "volume" in filled:
            filled.loc[missing, "volume"] = 0.0
        count = int(missing.sum())
        if count:
            warnings.warn(f"{name}: forward-filled {count} missing candles (<= {max_gap} consecutive)",
                          UserWarning, stacklevel=2)
        aligned.append(filled)
    common = aligned[0].index.intersection(aligned[1].index)
    return aligned[0].loc[common], aligned[1].loc[common]
