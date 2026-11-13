import pandas as pd
import pytest

from backtest.directional.engine import BacktestConfig, indicators, run_backtest


def bars(rows):
    return pd.DataFrame(rows, columns=['open', 'high', 'low', 'close'],
                        index=pd.date_range('2023-01-01', periods=len(rows), freq='h', tz='UTC'))


def simulate(rows, signals, **kwargs):
    frame = bars(rows)
    return run_backtest(frame, config=BacktestConfig(**kwargs),
                        signals=pd.Series(signals, index=frame.index))


def test_clean_uptrend_long_trailing():
    # Accelerating trend ensures positive MACD rather than a numerical near-zero histogram.
    prices = pd.Series([100 + i * i / 1000 for i in range(240)])
    frame = pd.DataFrame({'open': prices, 'high': prices + .05,
                          'low': prices - .05, 'close': prices})
    frame.index = pd.date_range('2023-01-01', periods=len(frame), freq='h', tz='UTC')
    # Once the trend has accumulated a >15% leveraged peak, give back five points.
    peak = float(prices.iloc[-1] + .05)
    extra = bars([(float(prices.iloc[-1]), peak, peak * .94, peak * .95)])
    extra.index = pd.DatetimeIndex([frame.index[-1] + pd.Timedelta(hours=1)])
    frame = pd.concat([frame, extra])
    result = run_backtest(frame, config=BacktestConfig(leverage=3))
    trade = result.trades.iloc[0]
    assert trade.side == 1
    assert trade.reason == 'trailing'
    assert trade.net_pnl > 0
    assert trade.exit_price == pytest.approx(peak - trade.entry_price * .05 / 3)


def test_exact_hard_stop_and_costs():
    result = simulate([(100, 100, 100, 100), (100, 101, 90, 95)], [1, 0], leverage=3,
                      slippage_bps=10)
    t = result.trades.iloc[0]
    entry = 100 * 1.001
    stop_fill = entry * .95 * .999
    margin = 500 / 1.0015
    qty = margin * 3 / entry
    fees = qty * (entry + stop_fill) * .0005
    assert t.reason == 'hard_stop'
    assert t.exit_price == pytest.approx(stop_fill)
    assert result.metrics['commissions'] == pytest.approx(fees)
    assert result.metrics['net_pnl'] == pytest.approx(qty * (stop_fill - entry) - fees)


def test_liquidation_high_leverage_gap():
    result = simulate([(100, 100, 100, 100), (100, 100.1, 99.9, 100),
                       (80, 81, 79, 80)], [1, 0, 0], leverage=20)
    t = result.trades.iloc[0]
    assert t.reason == 'liquidation'
    assert t.exit_price == 80
    assert result.metrics['liquidations'] == 1
    assert result.metrics['final_equity'] == pytest.approx(0, abs=1e-10)
    assert t.net_pnl == pytest.approx(-500)
    assert result.metrics['max_drawdown_pct'] == pytest.approx(100)


def test_no_lookahead_signal_or_execution():
    result = simulate([(100, 100, 100, 100), (120, 120, 120, 120)], [1, -1], fee=0)
    assert result.trades.iloc[0].entry_price == 120
    assert result.trades.iloc[0].side == 1
    frame = bars([(100 + i*i/1000, 101 + i*i/1000, 99 + i*i/1000,
                   100 + i*i/1000) for i in range(310)])
    original = indicators(frame)
    changed = frame.copy()
    changed.iloc[260:] *= 4
    pd.testing.assert_frame_equal(original.iloc[:260], indicators(changed).iloc[:260])


def test_exact_roundtrip_commissions():
    result = simulate([(100, 100, 100, 100)] * 3, [1, 0, 0], leverage=2)
    expected = (500 / 1.001) * 2 * .0005 * 2
    assert result.metrics['commissions'] == pytest.approx(expected)
    assert result.metrics['net_pnl'] == pytest.approx(-expected)


def test_strict_boundaries_and_adverse_first():
    exact = simulate([(100, 100, 100, 100), (100, 115, 85, 100)], [1, 0], leverage=1, fee=0)
    assert exact.trades.iloc[0].reason == 'end_of_data'  # peak ==15, low == -15
    stopped = simulate([(100, 100, 100, 100), (100, 130, 84, 110)], [1, 0], leverage=1, fee=0)
    assert stopped.trades.iloc[0].reason == 'hard_stop'
    assert stopped.trades.iloc[0].exit_price == 85


def test_short_trailing_and_price_reference():
    result = simulate([(100, 100, 100, 100), (100, 100, 90, 90), (90, 95, 89, 92)],
                      [-1, 0, 0], leverage=10, stop_mode='price', ref_leverage=3, fee=0)
    assert result.trades.iloc[0].reason == 'trailing'
    assert result.trades.iloc[0].exit_price == pytest.approx(90 + 100*.05/3)


def test_reentry_next_signal():
    result = simulate([(100, 100, 100, 100), (100, 100, 90, 95), (100, 100, 100, 100)],
                      [1, 1, 0], leverage=3)
    assert result.metrics['trades'] == 2


def test_funding_signed_actual_notional_and_only_while_held():
    frame = bars([(100, 100, 100, 100)] * 4)
    funding = pd.DataFrame({'funding_rate': [.01, .02, .03], 'mark_price': [110, 120, 130]},
                           index=[frame.index[0], frame.index[1] + pd.Timedelta(milliseconds=1), frame.index[2]])
    for side in (1, -1):
        result = run_backtest(frame, funding, BacktestConfig(leverage=2, fee=0),
                              signals=pd.Series([side, 0, 0, 0], index=frame.index))
        expected = -side * 10 * (120*.02 + 130*.03)
        assert result.metrics['net_funding'] == pytest.approx(expected)
        assert result.metrics['net_pnl'] == pytest.approx(expected)
        assert result.trades.net_pnl.sum() == pytest.approx(expected)


def test_normal_intrabar_stop_prevents_liquidation():
    result = simulate([(100, 100, 100, 100), (100, 101, 70, 80)], [1, 0], leverage=20, fee=0)
    assert result.trades.iloc[0].reason == 'hard_stop'
    assert result.trades.iloc[0].exit_price == pytest.approx(99.25)


def test_year_pnl_reconciles():
    result = simulate([(100, 100, 100, 100)] * 3, [1, 0, 0])
    assert sum(result.metrics['pnl_by_year'].values()) == pytest.approx(result.metrics['net_pnl'])


def test_strict_leveraged_equality_with_float_roundoff():
    result = simulate([(100, 100, 100, 100), (100, 105, 95, 100)], [1, 0], leverage=3, fee=0)
    assert result.trades.iloc[0].reason == 'end_of_data'


def test_gap_stop_still_fills_at_threshold_if_not_liquidated():
    result = simulate([(100, 100, 100, 100), (100, 100, 100, 100), (90, 90, 90, 90)],
                      [1, 0, 0], leverage=3, fee=0)
    assert result.trades.iloc[0].reason == 'hard_stop'
    assert result.trades.iloc[0].exit_price == 95


def test_liquidation_collateral_includes_prior_funding():
    frame = bars([(100, 100, 100, 100), (100, 100, 100, 100), (80, 80, 80, 80)])
    funding = pd.DataFrame({'funding_rate': [.001], 'mark_price': [100]},
                           index=[frame.index[1] + pd.Timedelta(milliseconds=1)])
    result = run_backtest(frame, funding, BacktestConfig(leverage=20),
                          signals=pd.Series([1, 0, 0], index=frame.index))
    assert result.trades.iloc[0].reason == 'liquidation'
    assert result.metrics['final_equity'] == pytest.approx(0, abs=1e-10)
    assert result.trades.net_pnl.sum() == pytest.approx(-500)


def test_favorable_open_gap_sets_peak_before_adverse_extreme():
    result = simulate([(100, 100, 100, 100), (100, 100, 100, 100), (110, 111, 106, 108)],
                      [1, 0, 0], leverage=3, fee=0)
    assert result.trades.iloc[0].reason == 'trailing'
    assert result.trades.iloc[0].exit_price == pytest.approx(110 - 100*.05/3)
