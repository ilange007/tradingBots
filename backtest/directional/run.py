"""Offline CLI, reading existing cache files without modifying them."""
import argparse
import json

import pandas as pd

from backtest.funding.data import CACHE_DIR, _read, utc
from .engine import BacktestConfig, indicators, run_backtest


def cached(kind, symbol, interval=""):
    paths = sorted(CACHE_DIR.glob(f"{kind}_{symbol}_{interval}_*.csv"))
    if not paths:
        raise ValueError(f"No existe caché {kind}/{symbol}/{interval}; modo sin red")
    # Merge overlapping cached requests deterministically; newest file wins.
    paths.sort(key=lambda p: (p.stat().st_mtime_ns, p.name))
    frame = pd.concat([_read(path) for path in paths])
    return frame.loc[~frame.index.duplicated(keep="last")].sort_index()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Estrategia direccional 1h Binance USDT-M; caché local, sin red")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", help="Fin UTC exclusivo; default: fin de la caché común")
    parser.add_argument("--capital", type=float, default=500)
    parser.add_argument("--leverage", type=float, default=3)
    parser.add_argument("--leverages", help="Comparar: 1,2,3,5,10,20")
    parser.add_argument("--margin-fraction", type=float, default=1)
    parser.add_argument("--fee", type=float, default=0.0005)
    parser.add_argument("--slippage-bps", type=float, default=0)
    parser.add_argument("--maintenance-margin", type=float, default=0.005)
    parser.add_argument("--stop-mode", choices=["roi", "price"], default="roi")
    parser.add_argument("--ref-leverage", type=float, default=3)
    parser.add_argument("--rsi-filter", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        symbol = args.symbol.upper()
        if not symbol.isalnum():
            raise ValueError("Símbolo inválido")
        leverages = [float(x) for x in args.leverages.split(",")] if args.leverages else [args.leverage]
        configs = [BacktestConfig(**{k: getattr(args, k) for k in (
            "capital", "margin_fraction", "fee", "slippage_bps", "maintenance_margin",
            "stop_mode", "ref_leverage", "rsi_filter")}, leverage=lev) for lev in leverages]
        perp, spot, funding = cached("perp", symbol, "1h"), cached("spot", symbol, "1h"), cached("funding", symbol)
        start = utc(args.start)
        end = utc(args.end) if args.end else min(perp.index[-1], spot.index[-1]) + pd.Timedelta(hours=1)
        if end <= start or start != start.floor("h") or end != end.floor("h"):
            raise ValueError("Rango inválido: fechas UTC alineadas a 1h, fin > inicio")
        history = perp.loc[(perp.index >= start - pd.Timedelta(hours=299)) & (perp.index < end)]
        active = history.loc[start:]
        grid = pd.date_range(start, end, freq="h", inclusive="left")
        if not active.index.equals(grid) or not pd.DatetimeIndex([grid[0], grid[-1]]).isin(spot.index).all():
            raise ValueError("La caché no cubre todas las velas del rango")
        # Funding schedules are variable. Require evidence on either side of the
        # complete requested interval, allowing the usual eight-hour spacing.
        if funding.empty or funding.index[0] > start or funding.index[-1] < end - pd.Timedelta(hours=8):
            raise ValueError("Cobertura de funding insuficiente")
        signals = indicators(history, args.rsi_filter).signal
        output = []
        for config in configs:
            result = run_backtest(history, funding, config, signals=signals, start=start, spot=spot)
            output.append(dict(symbol=symbol, start=start.isoformat(), end=end.isoformat(),
                               leverage=config.leverage, stop_mode=config.stop_mode, **result.metrics))
    except (ValueError, OSError, KeyError) as error:
        parser.exit(2, f"Error: {error}\n")
    if args.json:
        print(json.dumps(output if args.leverages else output[0], indent=2, allow_nan=False))
    else:
        table = pd.DataFrame(output).drop(columns=["symbol", "start", "end", "stop_mode", "pnl_by_year"])
        print(table.to_string(index=False, float_format=lambda x: f"{x:.4f}"))
        for row in output:
            print(f"PnL por año ({row['leverage']:g}x): {json.dumps(row['pnl_by_year'], sort_keys=True)}")
        print("PnL/ganancia/pérdida/comisiones/funding en USDT; porcentajes en columnas *_pct.")


if __name__ == "__main__":
    main()
