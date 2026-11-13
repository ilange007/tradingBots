"""Pure deterministic simulation; no I/O or exchange dependencies.

Frames use a UTC DatetimeIndex. Spot/perp require open/high/low/close; funding
requires funding_rate and optionally mark_price. Short candle gaps are filled
within the common coverage. Funding before the first candle is signal warmup only.
"""
from dataclasses import dataclass
import math

import numpy as np
import pandas as pd

from .data import align_candles


@dataclass(frozen=True)
class BacktestConfig:
    capital: float = 500.0
    spot_fraction: float = 0.5
    leverage: float = 2.0
    spot_fee: float = 0.001
    perp_fee: float = 0.0005
    slippage_bps: float = 0.0
    min_notional: float = 5.0
    funding_window: int = 3
    entry_threshold: float = 0.0
    exit_threshold: float = 0.0
    maintenance_margin: float = 0.005
    min_margin_ratio: float = 0.05
    rebalance_margin_ratio: float = 0.5
    liquidation_fee: float = 0.0125
    interval_hours: int = 8

    def __post_init__(self):
        numeric = [v for v in vars(self).values()]
        if not all(np.isfinite(v) for v in numeric):
            raise ValueError("All parameters must be finite")
        if self.capital <= 0 or not 0 < self.spot_fraction < 1 or self.leverage < 1:
            raise ValueError("Require capital > 0, 0 < spot_fraction < 1, leverage >= 1")
        if self.funding_window < 1 or int(self.funding_window) != self.funding_window:
            raise ValueError("funding_window must be a positive integer")
        if self.interval_hours not in (1, 8):
            raise ValueError("interval_hours must be 1 or 8")
        if min(self.spot_fee, self.perp_fee, self.slippage_bps, self.min_notional,
               self.maintenance_margin, self.min_margin_ratio, self.rebalance_margin_ratio, self.liquidation_fee) < 0:
            raise ValueError("Costs and margin parameters cannot be negative")
        if max(self.spot_fee, self.perp_fee, self.maintenance_margin, self.min_margin_ratio, self.rebalance_margin_ratio, self.liquidation_fee) >= 1 or self.slippage_bps >= 10000:
            raise ValueError("Fees, ratios and slippage must be less than 100%")


@dataclass
class BacktestResult:
    metrics: dict
    equity: pd.DataFrame
    trades: pd.DataFrame


def _frame(frame, required, name):
    frame = frame.copy(deep=True)
    if not isinstance(frame.index, pd.DatetimeIndex):
        raise ValueError(f"{name} requires a DatetimeIndex")
    frame.index = pd.to_datetime(frame.index, utc=True)
    if frame.index.has_duplicates or not frame.index.is_monotonic_increasing:
        raise ValueError(f"{name} timestamps must be unique and increasing")
    if not set(required).issubset(frame.columns):
        raise ValueError(f"{name} requires columns {required}")
    frame[list(required)] = frame[list(required)].astype(float)
    if not np.isfinite(frame[list(required)].to_numpy()).all():
        raise ValueError(f"{name} contains non-finite values")
    return frame


def run_backtest(spot, perp, funding, config=None):
    """Simulate matched spot/short legs, with next-open margin rebalancing.

    Cash includes posted collateral (a bookkeeping allocation). The high-based
    margin test precedes intrabar funding, whose ordering OHLC cannot identify.
    Liquidation forfeits posted collateral and charges a fee on high notional;
    the surviving spot leg is sold at this candle's close. min_margin_ratio is
    retained for compatibility and no longer triggers artificial stress fills.
    """
    c = config or BacktestConfig()
    spot = _frame(spot, ("open", "high", "low", "close"), "spot")
    perp = _frame(perp, ("open", "high", "low", "close"), "perp")
    funding = _frame(funding, ("funding_rate",), "funding")
    spot, perp = align_candles(spot, perp, c.interval_hours)
    step = pd.Timedelta(hours=c.interval_hours)
    for frame in (spot, perp):
        if (frame[["open", "high", "low", "close"]] <= 0).any().any():
            raise ValueError("Prices must be positive")
        if ((frame.high < frame[["open", "close", "low"]].max(axis=1)) |
                (frame.low > frame[["open", "close", "high"]].min(axis=1))).any():
            raise ValueError("Invalid OHLC bounds")
    cash, pos = c.capital, None
    fees = slip_cost = gross_funding = 0.0
    min_margin = None
    held_seconds = 0.0
    entries = rebalances = liquidations = 0
    rebalance_costs = 0.0
    pending_rebalance = False
    stress_curve = []
    trades, curve = [], []
    slip = c.slippage_bps / 10000
    signal = funding.funding_rate.rolling(c.funding_window, min_periods=c.funding_window).mean()

    def equity(s, p):
        return cash if pos is None else cash + pos["q"] * (s + pos["entry"] - p)

    def pay(events, fallback):
        nonlocal cash, gross_funding
        if pos is None:
            return
        for _, event in events.iterrows():
            mark = event.get("mark_price", np.nan)
            mark = float(mark) if pd.notna(mark) and np.isfinite(mark) and mark > 0 else fallback
            payment = pos["q"] * mark * event.funding_rate
            cash += payment
            pos["collateral"] += payment
            pos["funding"] += payment
            gross_funding += payment

    def close(t, s, p, reason):
        nonlocal cash, pos, fees, slip_cost
        q = pos["q"]
        sf, pf = s * (1 - slip), p * (1 + slip)
        cost = q * (sf * c.spot_fee + pf * c.perp_fee)
        cash += q * (sf + pos["entry"] - pf) - cost
        fees += cost
        slip_cost += q * (s + p) * slip
        trades.append(dict(entry_time=pos["time"], exit_time=t, quantity=q,
                           reason=reason, funding=pos["funding"],
                           pnl=cash - pos["initial_equity"]))
        pos = None

    def margin(price):
        return (pos["collateral"] + pos["q"] * (pos["entry"] - price)) / (pos["q"] * price)

    def rebalance(t, s, p):
        nonlocal cash, fees, slip_cost, rebalances, rebalance_costs
        if margin(p) >= 1 / c.leverage:
            return  # Price recovered before the queued rebalance.
        q = pos["q"]
        sf, pf = s * (1 - slip), p * (1 + slip)
        unit_fee = sf * c.spot_fee + pf * c.perp_fee
        proceeds = sf + pos["entry"] - pf - unit_fee
        target = p * (1 + 1 / c.leverage) - pos["entry"]
        # Minimal equal reduction that funds target collateral, including costs.
        reduction = (max(0.0, (q * target - cash) / (target + proceeds))
                     if target + proceeds > 0 else q)
        if reduction >= q:
            before_fees, before_slip = fees, slip_cost
            close(t, s, p, "rebalance")
            rebalance_costs += fees - before_fees + slip_cost - before_slip
            rebalances += 1
            return
        cash += reduction * proceeds
        cost = reduction * unit_fee
        slippage = reduction * (s + p) * slip
        fees += cost
        slip_cost += slippage
        rebalance_costs += cost + slippage
        pos["q"] -= reduction
        pos["collateral"] = pos["q"] * target
        rebalances += 1

    def liquidate(t, s, p):
        nonlocal cash, pos, fees, slip_cost, liquidations
        q = pos["q"]
        cost = q * (p * c.liquidation_fee + s * (1 - slip) * c.spot_fee)
        cash += q * s * (1 - slip) - pos["collateral"] - cost
        fees += cost
        slip_cost += q * s * slip
        trades.append(dict(entry_time=pos["time"], exit_time=t, quantity=q,
                           reason="liquidation", funding=pos["funding"],
                           pnl=cash - pos["initial_equity"]))
        liquidations += 1
        pos = None

    curve.append(dict(timestamp=spot.index[0], equity=cash))
    for t, s in spot.iterrows():
        p = perp.loc[t]
        end = t + step
        opening_equity = None
        pay(funding.loc[funding.index == t], p.open)
        prior = signal.loc[signal.index < t]
        avg = prior.iloc[-1] if len(prior) else np.nan
        if pos is not None:
            ratio = margin(p.open)
            min_margin = ratio if min_margin is None else min(min_margin, ratio)
            if ratio <= c.maintenance_margin:
                # Mark the surviving spot at open before its eventual close sale.
                opening_equity = (cash - pos["collateral"] + pos["q"] * s.open
                                  - pos["q"] * p.open * c.liquidation_fee)
                held_seconds += step.total_seconds()
                liquidate(end, s.close, p.open)
            elif pd.notna(avg) and avg < c.exit_threshold:
                close(t, s.open, p.open, "signal")
            elif pending_rebalance:
                rebalance(t, s.open, p.open)
        elif pd.notna(avg) and avg > c.entry_threshold and cash > 0:
            spot_budget, margin_budget = cash * c.spot_fraction, cash * (1 - c.spot_fraction)
            se, pe = s.open * (1 + slip), p.open * (1 - slip)
            q = min(spot_budget / (se * (1 + c.spot_fee)),
                    margin_budget / (pe * (1 / c.leverage + c.perp_fee)))
            collateral = margin_budget - q * pe * c.perp_fee
            initial_ratio = collateral / (q * pe)
            if min(q * se, q * pe) >= c.min_notional and initial_ratio > c.maintenance_margin:
                initial = cash
                entry_fees = q * (se * c.spot_fee + pe * c.perp_fee)
                cash -= q * se + entry_fees
                fees += entry_fees
                slip_cost += q * (s.open + p.open) * slip
                pos = dict(q=q, entry=pe, collateral=collateral, time=t,
                           funding=0.0, initial_equity=initial)
                entries += 1
        pending_rebalance = False
        curve.append(dict(timestamp=t, equity=(equity(s.open, p.open)
                                                if opening_equity is None else opening_equity)))
        stress_curve.append(curve[-1]["equity"])
        if pos is not None:
            stress_curve.append(equity(s.low, p.high))
            ratio = margin(p.high)
            min_margin = ratio if min_margin is None else min(min_margin, ratio)
            held_seconds += step.total_seconds()
            if ratio <= c.maintenance_margin:
                liquidate(end, s.close, p.high)
            else:
                pay(funding.loc[(funding.index > t) & (funding.index < end)], p.open)
                ratio = margin(p.high)
                min_margin = min(min_margin, ratio)
                if ratio <= c.maintenance_margin:
                    liquidate(end, s.close, p.high)
                else:
                    pending_rebalance = ratio < c.rebalance_margin_ratio
        if t == spot.index[-1] and pos is not None:
            close(end, s.close, p.close, "end")
        curve.append(dict(timestamp=end, equity=equity(s.close, p.close)))
        stress_curve.append(curve[-1]["equity"])
    history = pd.DataFrame(curve).set_index("timestamp")
    peak = history.equity.cummax()
    drawdown = (peak - history.equity) / peak
    elapsed = (spot.index[-1] + step - spot.index[0]).total_seconds()
    net = cash - c.capital
    exponent = math.log(cash / c.capital) * (365.25 * 86400 / elapsed) if cash > 0 else None
    annual = (math.expm1(exponent) * 100 if exponent < 700 else None) if exponent is not None else -100.0
    bh_q = c.capital / (spot.iloc[0].open * (1 + slip) * (1 + c.spot_fee))
    bh_final = bh_q * spot.iloc[-1].close * (1 - slip) * (1 - c.spot_fee)
    stress = pd.Series([c.capital] + stress_curve)
    stress_drawdown = (stress.cummax() - stress) / stress.cummax()
    metrics = dict(rebalances=rebalances, liquidations=liquidations,
                   rebalance_costs=rebalance_costs,
                   stress_drawdown_pct=float(stress_drawdown.max() * 100),
                   initial_capital=c.capital, final_equity=cash, net_pnl=net,
                   annualized_return_pct=annual, gross_funding=gross_funding,
                   fees=fees, slippage=slip_cost, total_costs=fees + slip_cost,
                   basis_pnl=net - gross_funding + fees + slip_cost,
                   max_drawdown_pct=float(drawdown.max() * 100),
                   time_in_position_pct=held_seconds / elapsed * 100, entries=entries,
                   min_margin_ratio=min_margin, buy_hold_pnl=bh_final - c.capital,
                   buy_hold_return_pct=(bh_final / c.capital - 1) * 100,
                   cash_pnl=0.0, excess_vs_buy_hold=net - (bh_final - c.capital))
    return BacktestResult(metrics, history, pd.DataFrame(trades, columns=[
        "entry_time", "exit_time", "quantity", "reason", "funding", "pnl"]))
