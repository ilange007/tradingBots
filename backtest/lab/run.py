"""CLI offline: python -m backtest.lab.run --symbols BTCUSDT,ETHUSDT."""
import argparse
import json
from pathlib import Path

import pandas as pd

from .engine import Config, portfolio, reports, simulate, utc, validate_hourly
from .strategies import NAMES, daily_candles, exposures


DATA_DIR = Path(__file__).resolve().parents[1] / "data_long"
SPLIT = utc("2025-01-01")
DESIGN_START = utc("2020-01-01")


def load(symbol):
    def read(suffix):
        frame = pd.read_csv(DATA_DIR / f"{symbol}_{suffix}.csv")
        frame.index = pd.DatetimeIndex(pd.to_datetime(frame.pop("timestamp"), utc=True, format="mixed"))
        return frame
    hourly, funding = read("perp_1h"), read("funding")
    validate_hourly(hourly)
    end = (hourly.index[-1] + pd.Timedelta(hours=1)).floor("D")
    if hourly.index[0] > DESIGN_START or end <= SPLIT:
        raise ValueError(f"{symbol}: faltan datos para diseño/reservado")
    # No se inventan pagos ausentes; exigir cobertura de ambos extremos.
    if funding.empty or funding.index[0] > DESIGN_START + pd.Timedelta(hours=1) or funding.index[-1] < end - pd.Timedelta(hours=8):
        raise ValueError(f"{symbol}: cobertura de funding insuficiente")
    return hourly, funding, end


def evaluate(symbols, capital=500.0, max_leverage=1.0):
    Config(capital=capital, max_leverage=max_leverage)
    data = {symbol: load(symbol) for symbol in symbols}
    # Ventana común para comparar símbolos y sumar la cartera.
    end = min(item[2] for item in data.values())
    predictions = {symbol: exposures(daily_candles(item[0]), max_leverage)
                   for symbol, item in data.items()}
    records = []
    for period, start, stop in (("design", DESIGN_START, SPLIT), ("holdout", SPLIT, end)):
        for strategy in NAMES:
            sleeves = {}
            for symbol, (hourly, funding, _) in data.items():
                target = predictions[symbol][strategy]
                if strategy == "buy_hold":
                    # Objetivo constante conocido antes del inicio del dataset.
                    seed = pd.Series([min(1.0, max_leverage)],
                                     index=pd.DatetimeIndex([DESIGN_START - pd.Timedelta(days=1)]))
                    target = pd.concat([seed, target])
                # Individuales usan capital completo; la cartera escala después.
                result = simulate(hourly, target, funding,
                                  Config(capital, max_leverage), start=start, end=stop)
                records.append(dict(symbol=symbol, period=period, strategy=strategy, **reports(result)))
                sleeves[symbol] = result
            if "BTCUSDT" in sleeves and "ETHUSDT" in sleeves:
                combined = portfolio(sleeves["BTCUSDT"], sleeves["ETHUSDT"])
                # Contabilidad lineal: dos cuentas de capital/2 equivalen a
                # dividir por dos todas las magnitudes monetarias combinadas.
                money = ["start_equity", "equity", "costs", "commissions", "slippage", "net_funding"]
                combined.daily[money] *= 0.5
                combined.initial_capital *= 0.5
                records.append(dict(symbol="BTC+ETH_50/50", period=period, strategy=strategy,
                                    **reports(combined)))
    return dict(capital=capital, max_leverage=max_leverage, records=records)


def print_table(output):
    rows = []
    for record in output["records"]:
        for interval, values in [(record["period"], record["metrics"]), *record["years"].items()]:
            rows.append(dict(symbol=record["symbol"], periodo=interval, estrategia=record["strategy"],
                             **{label: values[key] for label, key in (
                                 ("Anual%", "annualized_return_pct"), ("Vol%", "annual_volatility_pct"),
                                 ("Sharpe", "sharpe"), ("DD%", "max_drawdown_pct"),
                                 ("Ops", "operations"), ("Exp%", "exposed_time_pct"),
                                 ("Costes", "total_costs"), ("Funding", "net_funding"),
                                 ("PeorAño%", "worst_year_pct"), ("Liq", "liquidations"))}))
    frame = pd.DataFrame(rows)
    for symbol, group in frame.groupby("symbol", sort=False):
        print(f"\n{symbol} | capital inicial {output['capital']:g} USDT | L={output['max_leverage']:g}")
        print(group.drop(columns="symbol").sort_values(["periodo", "estrategia"]).to_string(
            index=False, float_format=lambda value: f"{value:.2f}"))
    print("\nUTC; diseño 2020–2024 y reservado 2025–fin reinician capital. Costes/funding en USDT.")
    print("Años parciales: Anual% extrapola; PeorAño% usa retorno observado. Sharpe: rf=0, 365 días.")
    print("Cartera: 50/50 inicial, sin rebalanceo entre cuentas. Liquidación al patrimonio <=0.")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Laboratorio diario offline de futuros USDT-M")
    parser.add_argument("--symbols", default="BTCUSDT,ETHUSDT")
    parser.add_argument("--capital", type=float, default=500.0)
    parser.add_argument("--max-leverage", type=float, default=1.0)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    symbols = [part.strip().upper() for part in args.symbols.split(",")]
    if not symbols or len(set(symbols)) != len(symbols) or any(
            symbol not in ("BTCUSDT", "ETHUSDT") for symbol in symbols):
        parser.error("--symbols admite BTCUSDT y/o ETHUSDT, sin duplicados")
    try:
        output = evaluate(symbols, args.capital, args.max_leverage)
    except (ValueError, OSError, KeyError) as error:
        parser.exit(2, f"Error: {error}\n")
    if args.json:
        print(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False))
    else:
        print_table(output)


if __name__ == "__main__":
    main()
