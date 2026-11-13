"""Delta-neutral spot / USDT-M perpetual funding backtester."""

from .engine import BacktestConfig, BacktestResult, run_backtest

__all__ = ["BacktestConfig", "BacktestResult", "run_backtest"]
