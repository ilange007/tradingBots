"""Contabilidad de futuros lineales USDT-M, pura y determinista."""
from dataclasses import dataclass
import math

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Config:
    capital: float = 500.0
    max_leverage: float = 1.0
    taker_fee: float = 0.0005
    slippage_bps: float = 2.0
    no_trade_band: float = 0.10

    def __post_init__(self):
        if not all(math.isfinite(v) for v in vars(self).values()):
            raise ValueError("Configuración no finita")
        if self.capital <= 0 or self.max_leverage <= 0:
            raise ValueError("capital y max_leverage deben ser positivos")
        if not 0 <= self.taker_fee < 1 or not 0 <= self.slippage_bps < 10000:
            raise ValueError("Costes inválidos")
        if not 0 <= self.no_trade_band <= 1:
            raise ValueError("Banda inválida")


@dataclass
class Result:
    daily: pd.DataFrame
    trades: pd.DataFrame
    initial_capital: float


def utc(value):
    stamp = pd.Timestamp(value)
    return stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")


def _index(frame, name):
    idx = frame.index
    if not isinstance(idx, pd.DatetimeIndex) or idx.tz is None:
        raise ValueError(f"{name}: índice temporal con zona horaria requerido")
    if str(idx.tz) != "UTC" or idx.has_duplicates or not idx.is_monotonic_increasing:
        raise ValueError(f"{name}: índice UTC único y creciente requerido")


def validate_hourly(hourly):
    _index(hourly, "OHLCV")
    if hourly.empty:
        raise ValueError("OHLCV vacío")
    grid = pd.date_range(hourly.index[0], hourly.index[-1], freq="h")
    if not hourly.index.equals(grid) or hourly.index[0] != hourly.index[0].floor("h"):
        raise ValueError("OHLCV debe ser horario sin huecos")
    prices = hourly[["open", "high", "low", "close"]].to_numpy(dtype=float)
    if not np.isfinite(prices).all() or (prices <= 0).any():
        raise ValueError("Precios inválidos")
    if (hourly.high < hourly[["open", "low", "close"]].max(axis=1)).any() or (
            hourly.low > hourly[["open", "high", "close"]].min(axis=1)).any():
        raise ValueError("OHLC inconsistente")
    if not np.isfinite(hourly.volume).all() or (hourly.volume < 0).any():
        raise ValueError("Volumen inválido")


def simulate(hourly, targets, funding=None, config=None, *, start=None, end=None):
    """Targets fechados por día de señal, ejecución al open del día siguiente.

    Cantidad constante entre órdenes. La banda compara nocional deseado contra
    nocional actual, ambos al open, dividido por patrimonio antes de costes.
    `end` es exclusivo. Cada ejecución de este motor arranca desde efectivo.
    """
    c = config or Config()
    validate_hourly(hourly)
    _index(targets, "Exposiciones")
    if not (targets.index == targets.index.normalize()).all():
        raise ValueError("Exposiciones deben estar fechadas a medianoche UTC")
    if not np.isfinite(targets.to_numpy()).all() or (targets.abs() > c.max_leverage).any():
        raise ValueError("Exposiciones fuera de [-L,+L]")
    start = utc(start) if start is not None else hourly.index[0]
    end = utc(end) if end is not None else hourly.index[-1] + pd.Timedelta(hours=1)
    if start != start.normalize() or end != end.normalize() or end <= start:
        raise ValueError("El intervalo debe contener días UTC completos")
    active = hourly.loc[(hourly.index >= start) & (hourly.index < end)]
    if not active.index.equals(pd.date_range(start, end, freq="h", inclusive="left")):
        raise ValueError("Cobertura horaria insuficiente")
    if funding is None:
        funding = pd.DataFrame(columns=["funding_rate", "mark_price"],
                               index=pd.DatetimeIndex([], tz="UTC"))
    _index(funding, "Funding")
    if not np.isfinite(funding.funding_rate.to_numpy(dtype=float)).all():
        raise ValueError("Funding inválido")
    marks = funding.mark_price.dropna().to_numpy(dtype=float)
    if not np.isfinite(marks).all() or (marks <= 0).any():
        raise ValueError("Mark price inválido")
    events = {}
    for event in funding.loc[(funding.index >= start) & (funding.index < end)].itertuples():
        events.setdefault(event.Index.floor("h"), []).append((event.funding_rate, event.mark_price))
    # Ausencia de historia previa => efectivo; nunca se rellena desde el futuro.
    execution = targets.copy()
    execution.index = execution.index + pd.Timedelta(days=1)
    execution = execution.to_dict()
    cash, quantity, previous = c.capital, 0.0, c.capital
    dead = False
    rows, trades = [], []

    def equity(price):
        return cash + quantity * price

    for bar in active.itertuples():
        day = bar.Index.normalize()
        if bar.Index == day:
            row = dict(day=day, start_equity=previous, costs=0.0, commissions=0.0,
                       slippage=0.0, net_funding=0.0, operations=0,
                       exposed_hours=0, exposure_mask=0, hours=0, liquidations=0,
                       target=float(execution.get(day, 0.0)), exposure_open=0.0)
        row["hours"] += 1
        if not dead and equity(bar.open) <= 0:
            cash, quantity, dead = 0.0, 0.0, True
            row["liquidations"] += 1
        if not dead and bar.Index == day:
            value = equity(bar.open)
            desired = row["target"] * value
            delta = desired - quantity * bar.open
            # Igualdad al 10% sí opera; tolerancia solo para redondeo flotante.
            if abs(delta) > 1e-12 and abs(delta) + value * 1e-12 >= c.no_trade_band * value:
                fee = abs(delta) * c.taker_fee
                slip = abs(delta) * c.slippage_bps / 10000
                quantity = desired / bar.open
                cash -= delta + fee + slip
                row["costs"] += fee + slip
                row["commissions"] += fee
                row["slippage"] += slip
                row["operations"] += 1
                trades.append(dict(timestamp=bar.Index, notional=delta, fee=fee,
                                   slippage=slip, target=row["target"], quantity=quantity))
            row["exposure_open"] = quantity * bar.open / value
        was_exposed = quantity != 0
        if not dead and equity(bar.open) <= 0:
            cash, quantity, dead = 0.0, 0.0, True
            row["liquidations"] += 1
        if not dead:
            for rate, mark in events.get(bar.Index, ()):
                payment = -quantity * (bar.open if pd.isna(mark) else mark) * rate
                cash += payment
                row["net_funding"] += payment
                if equity(bar.open) <= 0:
                    cash, quantity, dead = 0.0, 0.0, True
                    row["liquidations"] += 1
                    break
            adverse = bar.low if quantity >= 0 else bar.high
            if not dead and (equity(bar.open) <= 0 or equity(adverse) <= 0):
                cash, quantity, dead = 0.0, 0.0, True
                row["liquidations"] += 1
        row["exposed_hours"] += int(was_exposed)
        if was_exposed:
            row["exposure_mask"] |= 1 << bar.Index.hour
        if bar.Index.hour == 23:
            previous = max(0.0, equity(bar.close))
            row["equity"] = previous
            row["quantity_close"] = quantity
            rows.append(row)
    daily = pd.DataFrame(rows).set_index("day")
    return Result(daily, pd.DataFrame(trades, columns=["timestamp", "notional", "fee",
                                                    "slippage", "target", "quantity"]), c.capital)


def metrics(daily):
    """Métricas sobre marcas diarias. Retornos incluyen costes y funding."""
    if daily.empty:
        raise ValueError("Periodo vacío")
    initial = float(daily.start_equity.iloc[0])
    final = float(daily.equity.iloc[-1])
    returns = (daily.equity / daily.start_equity.replace(0, np.nan) - 1).fillna(0)
    std = float(returns.std(ddof=1)) if len(returns) > 1 else 0.0
    curve = pd.Series([initial, *daily.equity.to_list()], dtype=float)
    drawdown = (1 - curve / curve.cummax().replace(0, np.nan)).fillna(0)
    yearly = []
    for _, group in daily.groupby(daily.index.year):
        base = group.start_equity.iloc[0]
        yearly.append(float(group.equity.iloc[-1] / base - 1) * 100 if base > 0 else 0.0)
    total = final / initial - 1 if initial > 0 else 0.0
    annual = ((final / initial) ** (365 / len(daily)) - 1) if initial > 0 and final > 0 else (-1 if initial > 0 else 0)
    return dict(start=daily.index[0].date().isoformat(), end=daily.index[-1].date().isoformat(),
                initial_equity=initial, final_equity=final, total_return_pct=total * 100,
                annualized_return_pct=annual * 100, annual_volatility_pct=std * np.sqrt(365) * 100,
                sharpe=float(returns.mean() / std * np.sqrt(365)) if std > 1e-15 else 0.0,
                max_drawdown_pct=float(drawdown.max() * 100), operations=int(daily.operations.sum()),
                exposed_time_pct=float(daily.exposed_hours.sum() / daily.hours.sum() * 100),
                total_costs=float(daily.costs.sum()), commissions=float(daily.commissions.sum()),
                slippage=float(daily.slippage.sum()), net_funding=float(daily.net_funding.sum()),
                worst_year_pct=min(yearly), liquidations=int(daily.liquidations.sum()))


def reports(result):
    return dict(metrics=metrics(result.daily), years={str(year): metrics(group)
                for year, group in result.daily.groupby(result.daily.index.year)})


def portfolio(btc, eth):
    """Suma dos sleeves de capital inicial igual; sin rebalanceo cruzado."""
    if not btc.daily.index.equals(eth.daily.index) or btc.initial_capital != eth.initial_capital:
        raise ValueError("La cartera exige fechas y capitales iguales")
    columns = ["start_equity", "equity", "costs", "commissions", "slippage",
               "net_funding", "operations", "liquidations"]
    daily = btc.daily[columns] + eth.daily[columns]
    daily["hours"] = btc.daily.hours
    # Una hora cuenta como expuesta si al menos una cuenta mantiene posición.
    daily["exposed_hours"] = [int((a | b).bit_count()) for a, b in
                               zip(btc.daily.exposure_mask, eth.daily.exposure_mask)]
    return Result(daily, pd.DataFrame(), btc.initial_capital + eth.initial_capital)
