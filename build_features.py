import os
from datetime import datetime
import numpy as np
import pandas as pd
from dotenv import load_dotenv
from typing import Tuple
from dotenv import load_dotenv

load_dotenv()
ATR_PERIOD = 14
RSI_PERIOD = 14
STOCH_RSI_PERIOD = 14
STOCH_RSI_SMOOTH_K = 3
STOCH_RSI_SMOOTH_D = 3
SMA_FAST = 30
SMA_TREND = 200
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9

def ts():
    """Devuelve un string de la marca de tiempo UTC."""
    return datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')

def fetch_ohlcv_df(exchange, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
    """Descarga datos OHLCV para el análisis."""
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        return df
    except Exception as e:
        print(f"{ts()} | Error al obtener datos para {symbol}: {e}")
        return pd.DataFrame()
    
def calculate_rsi(df: pd.DataFrame, period: int) -> pd.Series:
    """
    Calcula el Índice de Fuerza Relativa (RSI) para una serie de precios de cierre.
    Maneja la división por cero si no hay pérdidas en el período.
    """
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()

    # Maneja la división por cero
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    
    rsi.loc[loss == 0] = 100
    rsi.loc[gain == 0] = 0

    return rsi

def calculate_stoch_rsi_series(rsi_series: pd.Series, period: int) -> Tuple[pd.Series, pd.Series]:
    """
    Calcula las series %K y %D del Stochastic RSI a partir de una serie RSI.
    """
    rsi_min = rsi_series.rolling(window=period).min()
    rsi_max = rsi_series.rolling(window=period).max()
    
    k = 100 * ((rsi_series - rsi_min) / (rsi_max - rsi_min))
    k.loc[rsi_max == rsi_min] = 50 # Manejar la división por cero
    
    d = k.rolling(window=period).mean()

    return k, d

def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """Calcula todos los indicadores técnicos y los añade al DataFrame."""
    if df.empty: return df
    
    df['SMA_FAST'] = df['close'].rolling(window=SMA_FAST).mean()
    df['SMA_TREND'] = df['close'].rolling(window=SMA_TREND).mean()
    
    ema_fast = df['close'].ewm(span=MACD_FAST, adjust=False).mean()
    ema_slow = df['close'].ewm(span=MACD_SLOW, adjust=False).mean()
    df['MACD'] = ema_fast - ema_slow
    df['MACD_SIGNAL'] = df['MACD'].ewm(span=MACD_SIGNAL, adjust=False).mean()
    
    df['tr'] = np.maximum(np.maximum(df['high'] - df['low'], abs(df['high'] - df['close'].shift(1))), abs(df['low'] - df['close'].shift(1)))
    df['ATR'] = df['tr'].rolling(window=ATR_PERIOD).mean()
    
    # Calcular y añadir RSI
    df['RSI'] = calculate_rsi(df, RSI_PERIOD)
    
    # Calcular y añadir StochRSI
    stoch_rsi_k, stoch_rsi_d = calculate_stoch_rsi_series(df['RSI'], STOCH_RSI_PERIOD)
    df['STOCH_RSI_K'] = stoch_rsi_k
    df['STOCH_RSI_D'] = stoch_rsi_d

    return df