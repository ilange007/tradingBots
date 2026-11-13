"""Deterministic hourly directional simulator; no exchange calls or mutable globals."""
from dataclasses import dataclass
import math

import pandas as pd


@dataclass(frozen=True)
class BacktestConfig:
    capital: float = 500
    leverage: float = 3
    margin_fraction: float = 1.0
    fee: float = 0.0005
    slippage_bps: float = 0
    maintenance_margin: float = 0.005
    stop_mode: str = "roi"
    ref_leverage: float = 3
    rsi_filter: bool = False

    def __post_init__(self):
        values = vars(self)
        if any(not math.isfinite(v) for v in values.values() if isinstance(v, (int, float))):
            raise ValueError("Parameters must be finite")
        if self.capital <= 0 or self.leverage < 1 or not 0 < self.margin_fraction <= 1:
            raise ValueError("capital > 0, leverage >= 1, 0 < margin_fraction <= 1 required")
        if not 0 <= self.fee < 1 or not 0 <= self.slippage_bps < 10000:
            raise ValueError("Invalid costs")
        if not 0 < self.maintenance_margin < 1 / self.leverage:
            raise ValueError("Maintenance margin must be below initial margin rate")
        if self.stop_mode not in ("roi", "price") or self.ref_leverage <= 0:
            raise ValueError("Invalid stop mode/reference leverage")


def _ema(series, length):
    """pandas_ta EMA: SMA seed followed by adjust=False EMA."""
    seeded = series.copy()
    seeded.iloc[:length - 1] = float("nan")
    seeded.iloc[length - 1] = series.iloc[:length].mean()
    return seeded.ewm(span=length, adjust=False).mean()


def indicators(candles, rsi_filter=False):
    """Match the bot's rolling fetch of 300 candles, including its EMA reseeding.

    Row t is a closed candle: production has a further, forming candle in its
    300-row fetch, hence at most 299 closed candles contribute at t.
    """
    close = candles.close.astype(float)
    out = pd.DataFrame(index=close.index)
    out["sma200"] = close.rolling(200).mean()
    hist, rsi_low, rsi_high = [], [], []
    for i in range(len(close)):
        if i < 199:
            hist.append(float("nan")); rsi_low.append(False); rsi_high.append(False)
            continue
        window = close.iloc[max(0, i - 298):i + 1]
        macd = _ema(window, 12) - _ema(window, 26)
        valid = macd.iloc[25:]
        hist.append(float((valid - _ema(valid, 9)).iloc[-1]))
        if rsi_filter:
            delta = window.diff()
            positive = delta.clip(lower=0).ewm(alpha=1 / 14, min_periods=14).mean()
            negative = (-delta.clip(upper=0)).ewm(alpha=1 / 14, min_periods=14).mean()
            rsi = 100 * positive / (positive + negative)
            rsi_low.append(bool(rsi.iloc[-3:].min() < 40))
            rsi_high.append(bool(rsi.iloc[-3:].max() > 60))
        else:
            rsi_low.append(True); rsi_high.append(True)
    out["macd_hist"] = hist
    out["signal"] = 0
    out.loc[(close > out.sma200) & (out.macd_hist > 0) & pd.Series(rsi_low, index=close.index), "signal"] = 1
    out.loc[(close < out.sma200) & (out.macd_hist < 0) & pd.Series(rsi_high, index=close.index), "signal"] = -1
    return out


@dataclass
class BacktestResult:
    metrics: dict
    trades: pd.DataFrame
    equity: pd.Series


def _validate(candles):
    if candles.empty or not isinstance(candles.index, pd.DatetimeIndex) or candles.index.tz is None:
        raise ValueError("Nonempty UTC-aware hourly candles required")
    if not candles.index.is_monotonic_increasing or candles.index.has_duplicates:
        raise ValueError("Candles must have unique ascending timestamps")
    if len(candles) > 1 and not (candles.index.to_series().diff().iloc[1:] == pd.Timedelta(hours=1)).all():
        raise ValueError("Missing or non-hourly candles")
    prices = candles[["open", "high", "low", "close"]]
    if prices.isna().any().any() or not prices.map(math.isfinite).all().all() or (prices <= 0).any().any():
        raise ValueError("Invalid OHLC prices")
    if ((candles.high < prices.max(axis=1)) | (candles.low > prices.min(axis=1))).any():
        raise ValueError("Inconsistent OHLC")


def run_backtest(candles, funding=None, config=None, *, signals=None, start=None, spot=None):
    """Signals at t execute at t+1 open. Explicit signals support synthetic tests.

    Event path: open (gap), funding, adverse extreme, favorable extreme, close.
    Intrabar exits are timestamped at bar end; open-gap exits at bar open.
    """
    c = config or BacktestConfig()
    _validate(candles)
    signals = indicators(candles, c.rsi_filter).signal if signals is None else signals.reindex(candles.index)
    if signals.isna().any() or not signals.isin([-1, 0, 1]).all():
        raise ValueError("Signals must be -1, 0 or 1 on each candle")
    if start is not None:
        start = pd.Timestamp(start)
        start = start.tz_localize("UTC") if start.tzinfo is None else start.tz_convert("UTC")
    active = candles if start is None else candles.loc[start:]
    if active.empty:
        raise ValueError("Empty trading interval")
    if funding is None:
        funding = pd.DataFrame(columns=["funding_rate", "mark_price"], index=pd.DatetimeIndex([], tz="UTC"))
    if not isinstance(funding.index, pd.DatetimeIndex) or funding.index.tz is None:
        raise ValueError("Funding requires timezone-aware DatetimeIndex")
    if not funding.index.is_monotonic_increasing or funding.index.has_duplicates:
        raise ValueError("Funding must have unique ascending timestamps")
    if not funding.empty and (funding.funding_rate.isna().any() or not funding.funding_rate.map(math.isfinite).all()):
        raise ValueError("Invalid funding rates")
    events = iter(funding.itertuples())
    event = next(events, None)
    wallet, fees, net_funding = c.capital, 0.0, 0.0
    position = None
    trades, points = [], [(active.index[0], wallet)]
    slip = c.slippage_bps / 10000
    scale = c.leverage if c.stop_mode == "roi" else c.ref_leverage

    def equity(price):
        return wallet if position is None else wallet + position["side"] * position["qty"] * (price - position["entry_price"])

    def close_position(raw, time, reason):
        nonlocal wallet, fees, position
        p = position
        fill = raw * (1 - p["side"] * slip)
        exit_fee = p["qty"] * fill * c.fee
        gross = p["side"] * p["qty"] * (fill - p["entry_price"])
        if reason == "liquidation":
            # Isolated collateral is confiscated, including liquidation settlement.
            # Taker closing fee is accounted inside that loss, never a second debit.
            gross = -p["margin"] - p["funding"] + exit_fee
        wallet += gross - exit_fee
        fees += exit_fee
        trades.append({**p, "exit_time": time, "exit_price": fill, "reason": reason,
                       "gross_pnl": gross, "exit_fee": exit_fee,
                       "net_pnl": gross - p["entry_fee"] - exit_fee + p["funding"]})
        position = None
        points.append((time, wallet))

    def limits():
        p = position
        stop_return = -0.15 / scale
        reason = "hard_stop"
        activation = 0.15 / scale
        if p["peak"] > activation and not math.isclose(p["peak"], activation, rel_tol=1e-12, abs_tol=1e-14):
            stop_return, reason = p["peak"] - 0.05 / scale, "trailing"
        stop = p["entry_price"] * (1 + p["side"] * stop_return)
        # collateral + signed q*(P-entry) = maintenance_rate*q*P
        collateral = p["margin"] + p["funding"]
        liq = (p["side"] * p["qty"] * p["entry_price"] - collateral) / (p["qty"] * (p["side"] - c.maintenance_margin))
        return stop, reason, liq

    def adverse(price, time, gap=False):
        if position is None:
            return
        p = position
        stop, reason, liq = limits()
        hit_liq = liq > 0 and p["side"] * (price - liq) <= 0
        # Hard stop is strictly below -15%; trailing inclusive at five points.
        distance = p["side"] * (price - stop)
        tolerance = p["entry_price"] * 1e-12
        hit_stop = distance <= tolerance if reason == "trailing" else distance < -tolerance
        if gap and hit_liq:
            close_position(price, time, "liquidation")
        elif hit_liq and (not hit_stop or p["side"] * (liq - stop) > 0):
            close_position(liq, time, "liquidation")
        elif hit_stop:
            close_position(stop, time, reason)
        elif hit_liq:
            close_position(liq, time, "liquidation")

    for time, bar in active.iterrows():
        end = time + pd.Timedelta(hours=1)
        trades_before_open = len(trades)
        adverse(bar.open, time, gap=True)
        # A close on this open must wait for the following closed-candle signal.
        closed_here = len(trades) > trades_before_open
        idx = candles.index.get_loc(time)
        signal = int(signals.iloc[idx - 1]) if idx else 0
        if position is None and not closed_here and signal and wallet > 1e-10:
            fill = bar.open * (1 + signal * slip)
            # Reserve entry commission so fraction=1 can actually be funded.
            margin = wallet * c.margin_fraction / (1 + c.leverage * c.fee)
            qty = margin * c.leverage / fill
            entry_fee = qty * fill * c.fee
            wallet -= entry_fee
            fees += entry_fee
            position = dict(side=signal, entry_time=time, entry_price=fill, qty=qty,
                            margin=margin, entry_fee=entry_fee, funding=0.0, peak=0.0)
            adverse(bar.open, time, gap=True)
        while event is not None and event.Index < end:
            if position is not None and event.Index >= time and event.Index > position["entry_time"]:
                mark = getattr(event, "mark_price", float("nan"))
                mark = mark if pd.notna(mark) and math.isfinite(mark) and mark > 0 else bar.open
                payment = -position["side"] * position["qty"] * mark * event.funding_rate
                wallet += payment
                position["funding"] += payment
                net_funding += payment
                adverse(bar.open, event.Index, gap=True)
            event = next(events, None)
        if position is not None:
            # The open is observed before either extreme, including favorable gaps.
            position["peak"] = max(position["peak"], position["side"] * (bar.open / position["entry_price"] - 1))
            adverse_price = bar.low if position["side"] == 1 else bar.high
            adverse(adverse_price, end)
            points.append((end, equity(adverse_price)))
        if position is not None:
            favorable = bar.high if position["side"] == 1 else bar.low
            position["peak"] = max(position["peak"], position["side"] * (favorable / position["entry_price"] - 1))
            points.append((end, equity(favorable)))
            adverse(bar.close, end)
        points.append((end, equity(bar.close)))
    if position is not None:
        close_position(active.close.iloc[-1], active.index[-1] + pd.Timedelta(hours=1), "end_of_data")
    curve = pd.Series([v for _, v in points], index=[t for t, _ in points], name="equity")
    trade_frame = pd.DataFrame(trades)
    pnls = [t["net_pnl"] for t in trades]
    wins, losses = [p for p in pnls if p > 0], [p for p in pnls if p < 0]
    streak = worst = 0
    for pnl in pnls:
        streak = streak + 1 if pnl < 0 else 0
        worst = max(streak, worst)
    annual = {}
    previous = c.capital
    calendar = pd.DatetimeIndex([curve.index[0], *(curve.index[1:] - pd.Timedelta(nanoseconds=1))])
    for year, group in curve.groupby(calendar.year):
        annual[str(year)] = float(group.iloc[-1] - previous)
        previous = group.iloc[-1]
    elapsed = (curve.index[-1] - curve.index[0]).total_seconds() / (365.25 * 86400)
    log_return = math.log(wallet / c.capital) / elapsed if wallet > 0 else None
    annualized = (math.expm1(log_return) * 100 if log_return is not None and log_return < 700 else None)
    if wallet <= 0:
        annualized = -100.0
    buy = spot.reindex(active.index) if spot is not None else None
    buy_pnl = None
    if buy is not None:
        if pd.isna(buy.open.iloc[0]) or pd.isna(buy.close.iloc[-1]):
            raise ValueError("Spot does not cover trading interval")
        buy_qty = c.capital / (buy.open.iloc[0] * (1 + slip) * (1 + c.fee))
        buy_pnl = buy_qty * buy.close.iloc[-1] * (1 - slip) * (1 - c.fee) - c.capital
    metrics = dict(net_pnl=wallet - c.capital, final_equity=wallet, annualized_return_pct=annualized,
                   max_drawdown_pct=float(((curve.cummax() - curve) / curve.cummax()).max() * 100),
                   trades=len(trades), win_rate_pct=100 * len(wins) / len(trades) if trades else 0,
                   average_win=sum(wins) / len(wins) if wins else 0,
                   average_loss=sum(losses) / len(losses) if losses else 0,
                   profit_factor=sum(wins) / -sum(losses) if losses else None,
                   commissions=fees, net_funding=net_funding,
                   liquidations=sum(t["reason"] == "liquidation" for t in trades),
                   worst_loss_streak=worst, pnl_by_year=annual, buy_hold_spot_pnl=buy_pnl)
    return BacktestResult(metrics, trade_frame, curve)
