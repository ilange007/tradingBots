"""CLI: python -m backtest.funding.run --symbol BTCUSDT --start 2023-01-01"""
import argparse
import json

import pandas as pd
import requests

from .data import download_funding, download_klines, utc
from .engine import BacktestConfig, run_backtest


def main(argv=None):
    parser = argparse.ArgumentParser(description="Arbitraje histórico de funding Binance: long spot + short USDT-M")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--start", required=True, help="Fecha UTC inclusiva")
    parser.add_argument("--end", help="Fecha UTC exclusiva (default: última vela completa)")
    parser.add_argument("--capital", type=float, default=500)
    parser.add_argument("--spot-fraction", type=float, default=0.5)
    parser.add_argument("--leverage", type=float, default=2)
    parser.add_argument("--spot-fee", type=float, default=0.001)
    parser.add_argument("--perp-fee", type=float, default=0.0005)
    parser.add_argument("--slippage-bps", type=float, default=0)
    parser.add_argument("--min-notional", type=float, default=5)
    parser.add_argument("--funding-window", type=int, default=3)
    parser.add_argument("--entry-threshold", type=float, default=0)
    parser.add_argument("--exit-threshold", type=float, default=0)
    parser.add_argument("--maintenance-margin", type=float, default=0.005)
    parser.add_argument("--min-margin-ratio", type=float, default=0.05)
    parser.add_argument("--rebalance-margin-ratio", type=float, default=0.5)
    parser.add_argument("--liquidation-fee", type=float, default=0.0125)
    parser.add_argument("--interval", choices=["1h", "8h"], default="8h")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--json", action="store_true", help="Solo métricas JSON")
    args = parser.parse_args(argv)
    try:
        config = BacktestConfig(**{name: getattr(args, name) for name in (
            "capital", "spot_fraction", "leverage", "spot_fee", "perp_fee", "slippage_bps",
            "min_notional", "funding_window", "entry_threshold", "exit_threshold",
            "maintenance_margin", "min_margin_ratio", "rebalance_margin_ratio", "liquidation_fee")}, interval_hours=int(args.interval[:-1]))
        start = utc(args.start)
        end = utc(args.end) if args.end else pd.Timestamp.now(tz="UTC").floor(args.interval)
        if end <= start or start != start.floor(args.interval) or end != end.floor(args.interval):
            raise ValueError("Inicio/fin deben estar alineados con el intervalo UTC; fin > inicio")
        warmup = start - pd.Timedelta(hours=8 * (args.funding_window + 3))
        funding = download_funding(args.symbol, warmup, end, refresh=args.refresh)
        spot = download_klines(args.symbol, start, end, interval=args.interval, refresh=args.refresh)
        perp = download_klines(args.symbol, start, end, market="perp", interval=args.interval, refresh=args.refresh)
        if spot.empty or spot.index[0] != start or spot.index[-1] + pd.Timedelta(args.interval) != end:
            raise ValueError("El histórico spot no cubre todo el rango solicitado")
        result = run_backtest(spot, perp, funding, config)
    except (ValueError, OSError, requests.RequestException) as error:
        parser.exit(2, f"Error: {error}\n")
    if args.json:
        print(json.dumps(result.metrics, indent=2, allow_nan=False))
        return
    m = result.metrics
    annual = "no representable" if m["annualized_return_pct"] is None else f'{m["annualized_return_pct"]:.2f}%'
    margin = "sin posición" if m["min_margin_ratio"] is None else f'{m["min_margin_ratio"] * 100:.2f}%'
    print(f"PnL neto: {m['net_pnl']:.6f} USDT | Anualizado: {annual}")
    print(f"Funding bruto: {m['gross_funding']:.6f} USDT")
    print(f"Comisiones + slippage: {m['total_costs']:.6f} USDT")
    print(f"PnL de base: {m['basis_pnl']:.6f} USDT")
    print(f"Max drawdown: {m['max_drawdown_pct']:.2f}% | Tiempo en posición: {m['time_in_position_pct']:.2f}%")
    print(f"Entradas: {m['entries']} | Margen mínimo observado: {margin}")
    print(f"Rebalanceos: {m['rebalances']} | Liquidaciones: {m['liquidations']} | Coste de rebalanceo: {m['rebalance_costs']:.6f} USDT")
    print(f"Buy & hold neto de costes: {m['buy_hold_pnl']:.6f} USDT | Efectivo: 0.000000 USDT")
    print(f"Exceso vs buy & hold: {m['excess_vs_buy_hold']:.6f} USDT")


if __name__ == "__main__":
    main()
