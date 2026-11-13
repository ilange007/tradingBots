import json
from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from backtest.funding.engine import BacktestConfig, run_backtest


def candles(n=6):
    index = pd.date_range("2023-01-01", periods=n, freq="8h", tz="UTC")
    frame = pd.DataFrame({k: np.full(n, 100.0) for k in ("open", "high", "low", "close")}, index=index)
    return frame


def funding(frame, rate=0.001):
    return pd.DataFrame({"funding_rate": rate, "mark_price": 100.0}, index=frame.index)


def test_constant_funding_exact_costs():
    s = candles()
    c = BacktestConfig(funding_window=1, slippage_bps=10)
    r = run_backtest(s, s, funding(s), c)
    q = (c.capital * c.spot_fraction) / (100 * 1.001 * 1.001)
    expected_funding = q * 100 * 0.001 * 4  # entry at t1; first receipt t2
    expected_fees = q * (100.1 * .001 + 99.9 * .0005 + 99.9 * .001 + 100.1 * .0005)
    expected_slip = q * .1 * 4
    assert r.metrics["entries"] == 1
    assert r.metrics["gross_funding"] == pytest.approx(expected_funding)
    assert r.metrics["fees"] == pytest.approx(expected_fees)
    assert r.metrics["slippage"] == pytest.approx(expected_slip)
    assert r.metrics["net_pnl"] == pytest.approx(expected_funding - expected_fees - expected_slip)
    assert r.metrics["basis_pnl"] == pytest.approx(0, abs=1e-10)
    assert r.metrics["final_equity"] == pytest.approx(r.equity.equity.iloc[-1])
    json.dumps(r.metrics, allow_nan=False)


def test_negative_funding_never_enters():
    s = candles()
    r = run_backtest(s, s, funding(s, -.001), BacktestConfig(funding_window=1))
    assert r.metrics["entries"] == 0
    assert r.metrics["net_pnl"] == 0
    assert r.metrics["min_margin_ratio"] is None


def test_extreme_perp_high_causes_liquidation():
    s = candles(3)
    p = s.copy()
    p.loc[p.index[1], "high"] = 200
    r = run_backtest(s, p, funding(s), BacktestConfig(funding_window=1))
    assert r.trades.iloc[0].reason == "liquidation"
    assert r.trades.iloc[0].exit_time == s.index[2]
    assert r.metrics["min_margin_ratio"] < .055
    assert r.metrics["net_pnl"] < 0
    assert r.metrics["liquidations"] == 1
    assert r.metrics["rebalances"] == 0


def test_no_lookahead_current_or_future_funding():
    s = candles(5)
    f = funding(s, -.001)
    f.loc[s.index[2]:, "funding_rate"] = .001
    r = run_backtest(s, s, f, BacktestConfig(funding_window=1))
    assert r.trades.iloc[0].entry_time == s.index[3]
    altered = f.copy()
    altered.loc[s.index[4], "funding_rate"] = 100
    changed = run_backtest(s, s, altered, BacktestConfig(funding_window=1))
    assert changed.trades.iloc[0].entry_time == r.trades.iloc[0].entry_time
    pd.testing.assert_frame_equal(s, candles(5))


def test_under_minimum_notional_never_trades():
    s = candles()
    r = run_backtest(s, s, funding(s), BacktestConfig(capital=5, funding_window=1, min_notional=5))
    assert r.metrics["entries"] == 0
    assert r.metrics["net_pnl"] == 0


def test_full_window_required_and_boundary_funding_not_paid_on_entry():
    s = candles()
    r = run_backtest(s, s, funding(s), BacktestConfig(funding_window=3))
    assert r.trades.iloc[0].entry_time == s.index[3]
    assert r.metrics["gross_funding"] == pytest.approx(r.trades.iloc[0].quantity * 100 * .001 * 2)


def test_negative_funding_paid_and_signal_exit_uses_prior_event():
    s = candles(4)
    f = funding(s)
    f.loc[s.index[2]:, "funding_rate"] = -.002
    r = run_backtest(s, s, f, BacktestConfig(funding_window=1))
    assert r.trades.iloc[0].exit_time == s.index[3]
    assert r.trades.iloc[0].reason == "signal"
    assert r.metrics["gross_funding"] == pytest.approx(-.004 * 100 * r.trades.iloc[0].quantity)


def test_allocation_and_leverage_limit_quantity():
    s = candles(3)
    c = BacktestConfig(funding_window=1, spot_fraction=.9, leverage=2)
    r = run_backtest(s, s, funding(s), c)
    assert r.trades.iloc[0].quantity == pytest.approx(50 / (100 * (.5 + .0005)))


def test_short_missing_candles_filled():
    full = candles()
    s = full.drop(full.index[2])
    with pytest.warns(UserWarning, match="forward-filled"):
        r = run_backtest(s, full, funding(full))
    assert r.metrics["liquidations"] == 0


def test_sixty_percent_rise_rebalances_both_legs_at_next_open():
    s = candles(5)
    s.loc[s.index[2]:, :] = 160.0
    # An adverse intrabar spot low must never become an execution price.
    s.loc[s.index[2], "low"] = 80.0
    c = BacktestConfig(funding_window=1, slippage_bps=5)
    r = run_backtest(s, s, funding(s), c)
    assert r.metrics["rebalances"] == 1
    assert r.metrics["liquidations"] == 0
    assert r.metrics["rebalance_costs"] > 0
    assert r.metrics["net_pnl"] == pytest.approx(
        r.metrics["gross_funding"] - r.metrics["total_costs"], abs=1e-10)
    assert r.metrics["stress_drawdown_pct"] > r.metrics["max_drawdown_pct"]
    # Rebalanced quantity restores equity/notional = 1/leverage at t3.
    initial_q = 250 / (100 * 1.0005 * 1.001)
    assert 0 < r.trades.iloc[0].quantity < initial_q
    next_open_equity = r.equity.loc[s.index[3], "equity"].iloc[-1]
    assert next_open_equity / (r.trades.iloc[0].quantity * 160) - 1 == pytest.approx(.5)


def test_liquidation_forfeits_collateral_and_sells_spot_at_close():
    s = candles(3)
    p = s.copy()
    p.loc[p.index[1], "high"] = 300
    c = BacktestConfig(funding_window=1, spot_fee=0, perp_fee=0)
    r = run_backtest(s, p, funding(s), c)
    assert r.metrics["final_equity"] == pytest.approx(250 - 2.5 * 300 * .0125)
    assert r.metrics["fees"] == pytest.approx(2.5 * 300 * .0125)
    assert r.metrics["liquidations"] == 1


def test_one_hour_funding_only_at_settlements():
    s = candles(10)
    s.index = pd.date_range("2023-01-01", periods=10, freq="h", tz="UTC")
    f = funding(s).iloc[[0, 8]]
    r = run_backtest(s, s, f, BacktestConfig(funding_window=1, interval_hours=1))
    assert r.trades.iloc[0].entry_time == s.index[1]
    assert r.metrics["gross_funding"] == pytest.approx(r.trades.iloc[0].quantity * .1)


@pytest.mark.parametrize("kwargs", [{"leverage": 0}, {"spot_fraction": 1}, {"capital": -1},
                                    {"slippage_bps": -1}, {"funding_window": 0}])
def test_invalid_config(kwargs):
    with pytest.raises(ValueError):
        BacktestConfig(**kwargs)


def test_queued_rebalance_cannot_prevent_opening_gap_liquidation():
    s = candles(5)
    s.loc[s.index[2], :] = 160.0
    s.loc[s.index[3]:, :] = 300.0
    f = funding(s)
    f.loc[s.index[3]:, "funding_rate"] = -0.001
    r = run_backtest(s, s, f, BacktestConfig(funding_window=1))
    assert r.trades.iloc[0].exit_time == s.index[4]
    assert r.metrics["liquidations"] == 1
    assert r.metrics["rebalances"] == 0


def test_recovered_open_does_not_force_queued_reduction():
    s = candles(5)
    p = s.copy()
    p.loc[p.index[2], "high"] = 160
    r = run_backtest(s, p, funding(s), BacktestConfig(funding_window=1))
    assert r.metrics["liquidations"] == 0
    assert r.metrics["rebalances"] == 0
    assert r.metrics["net_pnl"] == pytest.approx(
        r.metrics["gross_funding"] - r.metrics["total_costs"])
    assert r.metrics["max_drawdown_pct"] < 1
    assert r.metrics["stress_drawdown_pct"] > 20
