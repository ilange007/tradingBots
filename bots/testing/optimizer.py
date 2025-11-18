import os
import sys
import time
import math
from datetime import datetime
import numpy as np
import pandas as pd
import ccxt
from dotenv import load_dotenv
from typing import Tuple

# Add project root to Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

# Nota: Este script requiere el archivo 'combined_strategy.py' en la misma carpeta.
from utilities.combined_strategy import get_combined_signal

# ----------------------------- Utilidades -----------------------------

def ts():
    """Devuelve un string de la marca de tiempo UTC."""
    return datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')

def timeframe_to_seconds(timeframe: str) -> int:
    """Convierte un string de timeframe (ej. '1h', '4h') en segundos."""
    if not timeframe: return 300
    unit_map = {'m': 60, 'h': 3600, 'd': 86400, 'w': 604800, 'M': 2592000}
    try:
        value = int(timeframe[:-1])
        unit = timeframe[-1]
        return value * unit_map[unit]
    except (ValueError, IndexError):
        return 300

# ----------------------------- Configuración para Optimización ----------------------------

# Carga las variables de entorno para la configuración del exchange
load_dotenv()
EXCHANGE_ID = os.getenv("EXCHANGE", "binanceusdm").lower()

# Símbolo y Timeframe a optimizar
SYMBOL = 'BTC/USDT:USDT'
TIMEFRAME = '4h'

# Rango de parámetros para la optimización
# (ATR_K, TRAIL_R_MULTIPLE, MIN_VOLATILITY, MAX_VOLATILITY)
OPTIMIZATION_RANGES = {
    'ATR_K': np.arange(1.0, 4.1, 0.5),
    'TRAIL_R_MULTIPLE': np.arange(1.0, 5.1, 0.5),
    'MIN_VOLATILITY': np.arange(0.5, 1.1, 0.25),
    'MAX_VOLATILITY': np.arange(1.2, 2.1, 0.25)
}

# Parámetros de indicadores, pueden ser fijos durante la optimización
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

# Configuración del backtest
INITIAL_CAPITAL = 1000
DATA_LIMIT = 800
RISK_PER_TRADE = 0.01 # 1% de riesgo por operación
EXCHANGE_FEE = 0.0004 # 0.04% para Binance Futures

# ----------------------------- Funciones de Criptomonedas -----------------------------

def fetch_ohlcv_df(exchange, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
    """Descarga datos OHLCV para el backtest."""
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        return df
    except Exception as e:
        print(f"Error al obtener datos para {symbol}: {e}")
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

# ----------------------------- Lógica de Backtest -----------------------------

def run_backtest(df, atr_k: float, trail_r_multiple: float, min_volatility: float, max_volatility: float):
    """
    Ejecuta una simulación de backtest con los parámetros dados.
    Devuelve la ganancia total, el número de operaciones ganadoras/perdedoras.
    """
    capital = INITIAL_CAPITAL
    long_position = False
    short_position = False
    entry_price = 0
    trades = []
    
    # Calcula TODAS las características ANTES del bucle
    df_with_features = build_features(df.copy())
    
    # El bucle comienza una vez que todos los indicadores están listos
    start_index = max(SMA_TREND, MACD_SLOW, ATR_PERIOD, RSI_PERIOD, STOCH_RSI_PERIOD) + 20
    
    for i in range(start_index, len(df_with_features)):
        # Pasamos el DataFrame completo para que la estrategia pueda acceder a los datos
        current_data = df_with_features.iloc[:i]
        
        signal = get_combined_signal(current_data, min_volatility, max_volatility)
        
        if not long_position and not short_position:
            # Lógica de entrada
            if signal == 'BUY':
                entry_price = current_data['close'].iloc[-1]
                stop_loss_price = entry_price - (current_data['ATR'].iloc[-1] * atr_k)
                trailing_stop = entry_price - (current_data['ATR'].iloc[-1] * trail_r_multiple)
                long_position = True
            elif signal == 'SELL':
                entry_price = current_data['close'].iloc[-1]
                stop_loss_price = entry_price + (current_data['ATR'].iloc[-1] * atr_k)
                trailing_stop = entry_price + (current_data['ATR'].iloc[-1] * trail_r_multiple)
                short_position = True
        
        if long_position:
            current_low = current_data['low'].iloc[-1]
            current_close = current_data['close'].iloc[-1]
            
            # Actualiza el trailing stop si el precio se mueve a nuestro favor
            new_trailing_stop = current_close - (current_data['ATR'].iloc[-1] * trail_r_multiple)
            if new_trailing_stop > trailing_stop:
                trailing_stop = new_trailing_stop
            
            # Cierra la posición si el precio cae al stop fijo o al trailing stop
            if current_low <= stop_loss_price or current_low <= trailing_stop:
                exit_price = max(stop_loss_price, trailing_stop)
                pnl_pct = (exit_price - entry_price) / entry_price - EXCHANGE_FEE * 2
                trades.append(pnl_pct)
                long_position = False
            elif signal == 'SELL': # Cierre por señal contraria
                exit_price = current_close
                pnl_pct = (exit_price - entry_price) / entry_price - EXCHANGE_FEE * 2
                trades.append(pnl_pct)
                long_position = False
        
        elif short_position:
            current_high = current_data['high'].iloc[-1]
            current_close = current_data['close'].iloc[-1]
            
            # Actualiza el trailing stop si el precio se mueve a nuestro favor
            new_trailing_stop = current_close + (current_data['ATR'].iloc[-1] * trail_r_multiple)
            if new_trailing_stop < trailing_stop:
                trailing_stop = new_trailing_stop
            
            # Cierra la posición si el precio sube al stop fijo o al trailing stop
            if current_high >= stop_loss_price or current_high >= trailing_stop:
                exit_price = min(stop_loss_price, trailing_stop)
                pnl_pct = (entry_price - exit_price) / entry_price - EXCHANGE_FEE * 2
                trades.append(pnl_pct)
                short_position = False
            elif signal == 'BUY': # Cierre por señal contraria
                exit_price = current_close
                pnl_pct = (entry_price - exit_price) / entry_price - EXCHANGE_FEE * 2
                trades.append(pnl_pct)
                short_position = False
    
    if not trades:
        return 0, 0, 0
    
    total_gain = sum(p for p in trades if p > 0)
    total_loss = sum(p for p in trades if p < 0)
    win_rate = sum(1 for p in trades if p > 0) / len(trades)
    
    return total_gain + total_loss, len(trades), win_rate

def main_optimization():
    """Bucle principal de optimización."""
    exchange = getattr(ccxt, EXCHANGE_ID)({'enableRateLimit': True})
    
    print(f"{ts()} | Descargando datos para {SYMBOL} en {TIMEFRAME}...")
    df = fetch_ohlcv_df(exchange, symbol=SYMBOL, timeframe=TIMEFRAME, limit=DATA_LIMIT)
    
    if df.empty or len(df) < DATA_LIMIT:
        print("Datos insuficientes para la optimización. Por favor, ajuste el DATA_LIMIT o el símbolo.")
        return

    print(f"Datos descargados. Iniciando backtest para {len(df)} velas.")

    results = []
    total_iterations = len(OPTIMIZATION_RANGES['ATR_K']) * len(OPTIMIZATION_RANGES['TRAIL_R_MULTIPLE']) * \
                       len(OPTIMIZATION_RANGES['MIN_VOLATILITY']) * len(OPTIMIZATION_RANGES['MAX_VOLATILITY'])
    iteration_count = 0
    
    for atr_k in OPTIMIZATION_RANGES['ATR_K']:
        for trail_r_multiple in OPTIMIZATION_RANGES['TRAIL_R_MULTIPLE']:
            for min_volatility in OPTIMIZATION_RANGES['MIN_VOLATILITY']:
                for max_volatility in OPTIMIZATION_RANGES['MAX_VOLATILITY']:
                    iteration_count += 1
                    print(f"Iteración {iteration_count}/{total_iterations}: ATR_K={atr_k:.1f}, TRAIL_R_MULTIPLE={trail_r_multiple:.1f}, MIN_VOL={min_volatility:.2f}, MAX_VOL={max_volatility:.2f}")
                    
                    total_return, total_trades, win_rate = run_backtest(df, atr_k, trail_r_multiple, min_volatility, max_volatility)
                    
                    results.append({
                        'atr_k': atr_k,
                        'trail_r_multiple': trail_r_multiple,
                        'min_volatility': min_volatility,
                        'max_volatility': max_volatility,
                        'total_return_pct': total_return,
                        'total_trades': total_trades,
                        'win_rate': win_rate
                    })

    # Ordenar los resultados por ganancia total
    results.sort(key=lambda x: x['total_return_pct'], reverse=True)

    print("\n--- Resultados de la Optimización ---")
    print(f"Mejores parámetros para {SYMBOL} en {TIMEFRAME} (basado en retorno total):")
    print("---------------------------------------")
    
    # Imprimir los 5 mejores resultados
    for i, res in enumerate(results[:5]):
        print(f"Rank {i+1}:")
        print(f"  > ATR_K: {res['atr_k']:.1f}")
        print(f"  > TRAIL_R_MULTIPLE: {res['trail_r_multiple']:.1f}")
        print(f"  > MIN_VOLATILITY: {res['min_volatility']:.2f}")
        print(f"  > MAX_VOLATILITY: {res['max_volatility']:.2f}")
        print(f"  > Retorno Total: {res['total_return_pct'] * 100:.2f}%")
        print(f"  > Total de Operaciones: {res['total_trades']}")
        print(f"  > Tasa de Ganancia: {res['win_rate'] * 100:.2f}%")
        print("---------------------------------------")

if __name__ == "__main__":
    main_optimization()
