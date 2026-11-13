import json

import numpy as np
import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from backtest.lab.engine import Config, metrics, portfolio, reports, simulate, utc
from backtest.lab.strategies import NAMES, daily_candles, exposures, rsi_wilder


def hourly(days=3, price=100.0):
    index = pd.date_range("2024-01-01", periods=24 * days, freq="h", tz="UTC")
    return pd.DataFrame(dict(open=price, high=price, low=price, close=price, volume=1.0), index=index)


def signals(values, first="2023-12-31"):
    return pd.Series(values, index=pd.date_range(first, periods=len(values), freq="D", tz="UTC"))


def funding_at(times, rates, marks):
    return pd.DataFrame(dict(funding_rate=rates, mark_price=marks), index=pd.to_datetime(times, utc=True))


def test_no_lookahead_all_strategies_and_execution():
    bars = hourly(330)
    prices = 100 + np.arange(len(bars)) * 0.01 + np.sin(np.arange(len(bars)) / 20)
    for name in ("open", "high", "low", "close"):
        bars[name] = prices
    changed = bars.copy()
    cutoff = utc("2024-10-01")
    changed.loc[cutoff:, ["open", "high", "low", "close"]] *= 0.25
    before, after = exposures(daily_candles(bars)), exposures(daily_candles(changed))
    assert tuple(before.columns) == NAMES
    assert_frame_equal(before.loc[before.index < cutoff], after.loc[after.index < cutoff])
    for name in NAMES:
        a = simulate(bars, before[name]).daily
        b = simulate(changed, after[name]).daily
        assert_frame_equal(a.loc[a.index < cutoff], b.loc[b.index < cutoff])


def test_signal_executes_only_next_open():
    bars = hourly(2)
    result = simulate(bars, signals([1, 0], first="2024-01-01"))
    assert result.daily.exposure_open.to_list() == [0, 1]
    assert result.trades.timestamp.to_list() == [utc("2024-01-02")]
    assert result.daily.equity.iloc[-1] == pytest.approx(499.65)


def test_exact_costs_entry_and_exit():
    result = simulate(hourly(2), signals([1, 0]))
    assert result.trades.notional.to_list() == [500, -500]
    assert result.trades.fee.to_list() == pytest.approx([0.25, 0.25])
    assert result.trades.slippage.to_list() == pytest.approx([0.10, 0.10])
    assert result.daily.costs.sum() == pytest.approx(0.70)
    assert result.daily.equity.iloc[-1] == pytest.approx(499.30)
    assert metrics(result.daily)["operations"] == 2


@pytest.mark.parametrize("price", [97.31, 101.13, 109.37, 203.17])
def test_exit_leaves_no_dust_position_or_funding(price):
    events = funding_at(["2024-01-02 08:00"], [0.001], [price])
    result = simulate(hourly(2, price), signals([1, 0]), events)
    assert result.daily.quantity_close.iloc[-1] == 0
    assert result.daily.exposed_hours.to_list() == [24, 0]
    assert result.daily.net_funding.sum() == 0


@pytest.mark.parametrize("side,rate,expected", [(1, 0.001, -0.5), (-1, 0.001, 0.5),
                                               (1, -0.001, 0.5), (-1, -0.001, -0.5)])
def test_funding_sign(side, rate, expected):
    events = funding_at(["2024-01-01 08:00:00.003"], [rate], [np.nan])
    result = simulate(hourly(1), signals([side]), events)
    assert result.daily.net_funding.sum() == pytest.approx(expected)
    assert result.daily.equity.iloc[-1] == pytest.approx(499.65 + expected)


def test_funding_uses_real_mark_and_each_event():
    events = funding_at(["2024-01-01 08:00", "2024-01-01 16:00"], [0.001, 0.002], [120, 110])
    result = simulate(hourly(1), signals([1]), events)
    assert result.daily.net_funding.sum() == pytest.approx(-5 * (120 * 0.001 + 110 * 0.002))


def test_funding_at_open_follows_rebalance_and_flat_pays_zero():
    events = funding_at(["2024-01-01 00:00", "2024-01-02 00:00"], [0.001, 0.001], [100, 100])
    result = simulate(hourly(2), signals([1, 0]), events)
    assert result.daily.net_funding.to_list() == pytest.approx([-0.5, 0])


def test_no_trade_band_uses_current_notional():
    bars = hourly(3)
    bars.loc["2024-01-03":, ["open", "high", "low", "close"]] = 200
    result = simulate(bars, signals([1, 0.95, 0.95]))
    assert len(result.trades) == 1  # Pese al gap, delta sigue por debajo del 10% del patrimonio.
    assert result.daily.quantity_close.to_list() == [5, 5, 5]
    flat = simulate(hourly(2), signals([0.09, 0.10]))
    assert flat.trades.timestamp.to_list() == [utc("2024-01-02")]
    assert flat.trades.notional.iloc[0] == pytest.approx(50)


def test_drift_triggers_rebalance_even_with_unchanged_target():
    bars = hourly(2)
    bars.loc["2024-01-02":, ["open", "high", "low", "close"]] = 150
    result = simulate(bars, signals([0.5, 0.5]))
    assert len(result.trades) == 2
    assert result.trades.notional.iloc[1] < 0


def test_clean_uptrend_sma_gains_after_costs():
    bars = hourly(260)
    # Cada open coincide con el cierre anterior, sin gaps artificiales.
    bars["open"] = 100 + np.arange(len(bars)) * 0.01
    bars["close"] = bars.open + 0.01
    bars["low"], bars["high"] = bars.open, bars.close
    target = exposures(daily_candles(bars)).sma_trend
    result = simulate(bars, target)
    assert target.iloc[:199].eq(0).all()
    assert result.trades.timestamp.iloc[0] == bars.index[0] + pd.Timedelta(days=200)
    q = 500 / bars.open.loc[result.trades.timestamp.iloc[0]]
    gross = q * (bars.close.iloc[-1] - bars.open.loc[result.trades.timestamp.iloc[0]])
    assert result.daily.equity.iloc[-1] == pytest.approx(500 + gross - 0.35)
    assert result.daily.equity.iloc[-1] > 500


@pytest.mark.parametrize("side,extreme,price", [(1, "low", 89), (-1, "high", 111)])
def test_high_leverage_liquidation_is_absorbing(side, extreme, price):
    bars = hourly(3)
    bars.loc["2024-01-01 12:00", extreme] = price
    result = simulate(bars, signals([side * 10] * 3), config=Config(max_leverage=10))
    assert result.daily.equity.to_list() == [0, 0, 0]
    assert result.daily.liquidations.sum() == 1
    assert len(result.trades) == 1
    assert metrics(result.daily)["max_drawdown_pct"] == 100
    assert metrics(result.daily)["annualized_return_pct"] == -100


def test_open_gap_liquidation_precedes_new_signal():
    bars = hourly(2)
    bars.loc["2024-01-02":, ["open", "high", "low", "close"]] = 80
    result = simulate(bars, signals([5, 0]), config=Config(max_leverage=5))
    assert result.daily.liquidations.iloc[1] == 1
    assert len(result.trades) == 1


def test_donchian_excludes_current_bar_and_keeps_state():
    bars = hourly(80)
    bars.loc[bars.index.normalize() == utc("2024-02-25"), ["open", "high", "low", "close"]] = 110
    daily = daily_candles(bars)
    target = exposures(daily).donchian
    assert target.iloc[:55].eq(0).all()
    assert target.iloc[55] == 1
    assert target.iloc[56] == 1
    daily.loc[daily.index[57], ["open", "high", "low", "close"]] = 90
    assert exposures(daily).donchian.iloc[57] == 0


def test_fixed_parameters_limits_and_ensemble():
    bars = hourly(300)
    for name in ("open", "high", "low", "close"):
        bars[name] = 100 + np.arange(len(bars)) * 0.01
    target = exposures(daily_candles(bars), 3)
    assert target.sma_trend.iloc[-1] == 1
    assert target.sma_cross_ls.iloc[-1] == 1
    assert target.tsmom_vol.iloc[-1] == 3
    np.testing.assert_allclose(target.ensemble, target[["sma_trend", "donchian", "tsmom_vol"]].mean(axis=1))
    assert exposures(daily_candles(bars), 0.5).abs().max().max() <= 0.5


def test_wilder_seed_and_incomplete_day():
    close = pd.Series([100, 99, 101, 100, 100], dtype=float)
    assert rsi_wilder(close).iloc[2] == pytest.approx(100 * 2 / 3)
    assert rsi_wilder(close).iloc[3] == pytest.approx(40)
    assert len(daily_candles(hourly(2).iloc[:-1])) == 1


def test_holdout_uses_history_but_resets_equity_and_years_partition():
    bars = hourly(370)
    target = signals([1] * 371)
    result = simulate(bars, target, start="2025-01-01")
    assert result.daily.start_equity.iloc[0] == 500
    assert result.trades.timestamp.iloc[0] == utc("2025-01-01")
    assert result.daily.equity.iloc[-1] == pytest.approx(499.65)
    full = simulate(bars, target)
    report = reports(full)
    assert set(report["years"]) == {"2024", "2025"}
    assert sum(year["operations"] for year in report["years"].values()) == report["metrics"]["operations"]
    json.dumps(report, allow_nan=False)


def test_portfolio_sums_sleeves_and_counts_union_of_hours():
    bars = hourly(2)
    a = simulate(bars, signals([1, 0]), config=Config(capital=250))
    b = simulate(bars, signals([1, 1]), config=Config(capital=250))
    result = portfolio(a, b)
    assert result.initial_capital == 500
    np.testing.assert_allclose(result.daily.equity, a.daily.equity + b.daily.equity)
    assert result.daily.exposed_hours.to_list() == [24, 24]
    assert result.daily.operations.sum() == 3
    assert result.daily.costs.sum() == pytest.approx(0.525)


def test_cash_and_determinism_do_not_mutate_inputs():
    bars = hourly(3)
    original = bars.copy(deep=True)
    targets = signals([0, 0, 0])
    a, b = simulate(bars, targets), simulate(bars, targets)
    assert_frame_equal(a.daily, b.daily)
    assert_frame_equal(bars, original)
    report = metrics(a.daily)
    assert report["sharpe"] == 0
    assert report["annual_volatility_pct"] == 0
    assert report["total_return_pct"] == 0
    assert report["exposed_time_pct"] == 0


def test_invalid_inputs_rejected():
    with pytest.raises(ValueError, match="sin huecos"):
        simulate(hourly(2).drop(hourly(2).index[4]), signals([0]))
    with pytest.raises(ValueError, match="fuera"):
        simulate(hourly(1), signals([2]))
    with pytest.raises(ValueError):
        Config(max_leverage=float("nan"))
