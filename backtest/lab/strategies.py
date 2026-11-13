"""Exposiciones al cierre diario; el motor aplica un desplazamiento de un día."""
import math

import numpy as np
import pandas as pd


NAMES = ("sma_trend", "sma_cross_ls", "donchian", "tsmom_vol", "rsi2_dip",
         "ensemble", "buy_hold", "cash")


def daily_candles(hourly):
    """Solo días UTC completos: no se crean señales con velas en formación."""
    daily = hourly.resample("1D").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
    return daily.loc[hourly.close.resample("1D").count().eq(24)]


def rsi_wilder(close, length=2):
    """RSI de Wilder, semilla SMA de los primeros `length` cambios."""
    delta = close.diff()
    gain, loss = delta.clip(lower=0), -delta.clip(upper=0)
    def smooth(values):
        seeded = pd.Series(np.nan, index=values.index)
        if len(values) > length:
            seeded.iloc[length] = values.iloc[1:length + 1].mean()
            seeded.iloc[length + 1:] = values.iloc[length + 1:]
        return seeded.ewm(alpha=1 / length, adjust=False).mean()
    up, down = smooth(gain), smooth(loss)
    return (100 * up / (up + down)).mask((up + down).eq(0), 50)


def exposures(daily, max_leverage=1.0):
    """Parámetros fijos; cada fila usa únicamente información hasta ese cierre."""
    if not math.isfinite(max_leverage) or max_leverage <= 0:
        raise ValueError("max_leverage debe ser positivo y finito")
    close = daily.close
    sma200, sma50 = close.rolling(200).mean(), close.rolling(50).mean()
    unit = min(1.0, max_leverage)
    out = pd.DataFrame(index=daily.index)
    out["sma_trend"] = (close > sma200).astype(float) * unit
    out["sma_cross_ls"] = np.sign(sma50 - sma200).fillna(0) * unit
    # El canal excluye la vela de ruptura. Se exige cierre fuera del canal.
    upper = daily.high.shift(1).rolling(55).max()
    lower = daily.low.shift(1).rolling(20).min()
    held, channel = False, []
    for price, hi, lo in zip(close, upper, lower):
        if held and price < lo:
            held = False
        elif not held and price > hi:
            held = True
        channel.append(unit if held else 0.0)
    out["donchian"] = channel
    vol = close.pct_change(fill_method=None).rolling(30).std(ddof=1) * np.sqrt(365)
    size = (0.40 / vol).clip(upper=max_leverage)
    # Con volatilidad cero y momentum positivo se usa el límite L.
    out["tsmom_vol"] = size.where(close > close.shift(90), 0).fillna(0)
    rsi, sma5 = rsi_wilder(close), close.rolling(5).mean()
    held, dips = False, []
    for price, trend, fast, value in zip(close, sma200, sma5, rsi):
        if held and price > fast:
            held = False
        elif not held and price > trend and value < 10:
            held = True
        dips.append(unit if held else 0.0)
    out["rsi2_dip"] = dips
    out["ensemble"] = out[["sma_trend", "donchian", "tsmom_vol"]].mean(axis=1)
    out["buy_hold"] = unit
    out["cash"] = 0.0
    return out.loc[:, NAMES].clip(-max_leverage, max_leverage)
