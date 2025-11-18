import os
import time
import math
from datetime import datetime
import numpy as np
import pandas as pd
import ccxt
from dotenv import load_dotenv
from typing import Tuple

# Nota: Este script requiere el archivo 'combined_strategy.py' en la misma carpeta.
from combined_strategy import get_combined_signal

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

# ----------------------------- Configuración del Bot de Trading ----------------------------

# Carga las variables de entorno para la configuración del exchange
load_dotenv()
EXCHANGE_ID = os.getenv("EXCHANGE", "binanceusdm").lower()
API_KEY = os.getenv("API_KEY", "")
API_SECRET = os.getenv("API_SECRET", "")
USE_TESTNET = os.getenv("USE_TESTNET", "False").lower() == "true"
INITIAL_CAPITAL = float(os.getenv("INITIAL_CAPITAL", "1000"))
RISK_PER_TRADE = float(os.getenv("RISK_PER_TRADE", "0.01"))
# SYMBOL ahora es una lista
SYMBOLS_TO_TRADE = [s.strip() for s in os.getenv("SYMBOLS", "BTC/USDT:USDT").split(',')]
TIMEFRAME = os.getenv("TIMEFRAME", "4h")
LOOP_SLEEP_SEC = timeframe_to_seconds(TIMEFRAME)

# Parámetros optimizados
ATR_K = float(os.getenv("ATR_K", "2.5"))
TRAIL_R_MULTIPLE = float(os.getenv("TRAIL_R_MULTIPLE", "4.0"))
MIN_VOLATILITY = float(os.getenv("MIN_VOLATILITY", "1.00"))
MAX_VOLATILITY = float(os.getenv("MAX_VOLATILITY", "1.45"))

# Parámetros de indicadores (deben ser los mismos que en el optimizer)
ATR_PERIOD = 14
RSI_PERIOD = 14
STOCH_RSI_PERIOD = 14
SMA_FAST = 30
SMA_TREND = 200
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9

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

# ----------------------------- Lógica del Bot -----------------------------

def calculate_position_size(balance: float, stop_loss_usd: float) -> float:
    """Calcula el tamaño de la posición en base al riesgo y el stop loss."""
    if stop_loss_usd <= 0:
        return 0
    risk_usd = balance * RISK_PER_TRADE
    return risk_usd / stop_loss_usd

def execute_trade(exchange, symbol: str, side: str, amount: float, price: float):
    """Ejecuta una orden de mercado en el exchange."""
    try:
        order = exchange.create_order(symbol, 'market', side, amount, price)
        print(f"{ts()} | ¡TRADE EJECUTADO! {side.upper()} {amount:.4f} {symbol} a {price:.2f}.")
        return order
    except ccxt.NetworkError as e:
        print(f"{ts()} | Error de red al ejecutar la orden para {symbol}: {e}")
        return None
    except ccxt.ExchangeError as e:
        print(f"{ts()} | Error del exchange al ejecutar la orden para {symbol}: {e}")
        return None

def close_position(exchange, symbol: str, side: str, amount: float):
    """Cierra la posición abierta."""
    try:
        if side == 'long':
            side = 'sell'
        else:
            side = 'buy'
            
        close_order = exchange.create_order(symbol, 'market', side, amount)
        print(f"{ts()} | POSICIÓN CERRADA. {side.upper()} {amount:.4f} {symbol}.")
        return close_order
    except ccxt.NetworkError as e:
        print(f"{ts()} | Error de red al cerrar la posición para {symbol}: {e}")
        return None
    except ccxt.ExchangeError as e:
        print(f"{ts()} | Error del exchange al cerrar la posición para {symbol}: {e}")
        return None

def main_execution_loop():
    """Bucle principal de ejecución del bot."""
    
    # 1. Conectar al exchange
    print(f"\n{ts()} | Conectando al exchange {EXCHANGE_ID}...")
    try:
        exchange = getattr(ccxt, EXCHANGE_ID)({
            'apiKey': API_KEY,
            'secret': API_SECRET,
            'options': {'defaultType': 'future'},
            'enableRateLimit': True,
        })
        if USE_TESTNET:
            exchange.set_sandbox_mode(True)
        print(f"{ts()} | Conexión exitosa. Modo Testnet: {USE_TESTNET}")
    except Exception as e:
        print(f"{ts()} | Error al conectar con el exchange: {e}")
        return

    # 2. Bucle principal
    position = None # (symbol, side, entry_price, size, trailing_stop)
    
    print(f"\n{ts()} | Bot de ejecución iniciado para símbolos: {SYMBOLS_TO_TRADE} en {TIMEFRAME}. Repitiendo cada {LOOP_SLEEP_SEC} segundos.")
    print("Presione Ctrl+C para salir.\n")
    
    while True:
        try:
            if position:
                # Lógica de gestión de posición: solo para el símbolo actual en trade
                symbol, side, entry_price, size, trailing_stop = position
                
                print(f"{ts()} | Gestionando posición abierta en {symbol}...")
                
                # a. Obtener datos para el símbolo en trade
                df = fetch_ohlcv_df(exchange, symbol=symbol, timeframe=TIMEFRAME, limit=SMA_TREND + 50)
                if df.empty or len(df) < SMA_TREND:
                    print(f"{ts()} | Datos insuficientes para {symbol}, esperando...")
                    time.sleep(LOOP_SLEEP_SEC)
                    continue
                
                df = build_features(df)
                current_close = df['close'].iloc[-1]
                current_atr = df['ATR'].iloc[-1]
                
                # b. Actualizar trailing stop
                if side == 'BUY':
                    new_trailing_stop = current_close - (current_atr * TRAIL_R_MULTIPLE)
                    if new_trailing_stop > trailing_stop:
                        trailing_stop = new_trailing_stop
                        position = (symbol, side, entry_price, size, trailing_stop)
                        print(f"{ts()} | Trailing Stop actualizado para {symbol} a: {trailing_stop:.2f}")
                    if current_close <= trailing_stop:
                        print(f"{ts()} | Stop Loss o Trailing Stop alcanzado. Cerrando posición LONG en {symbol}.")
                        close_position(exchange, symbol, 'long', size)
                        position = None
                
                elif side == 'SELL':
                    new_trailing_stop = current_close + (current_atr * TRAIL_R_MULTIPLE)
                    if new_trailing_stop < trailing_stop:
                        trailing_stop = new_trailing_stop
                        position = (symbol, side, entry_price, size, trailing_stop)
                        print(f"{ts()} | Trailing Stop actualizado para {symbol} a: {trailing_stop:.2f}")
                    if current_close >= trailing_stop:
                        print(f"{ts()} | Stop Loss o Trailing Stop alcanzado. Cerrando posición SHORT en {symbol}.")
                        close_position(exchange, symbol, 'short', size)
                        position = None
                        
                # c. Cierre por señal contraria
                signal = get_combined_signal(df, MIN_VOLATILITY, MAX_VOLATILITY)
                if (side == 'BUY' and signal == 'SELL') or (side == 'SELL' and signal == 'BUY'):
                    print(f"{ts()} | Señal contraria detectada. Cerrando posición en {symbol}.")
                    close_position(exchange, symbol, side, size)
                    position = None
            
            else: # Sin posición abierta, escanear símbolos
                print(f"{ts()} | Sin posición abierta. Escaneando símbolos...")
                for symbol in SYMBOLS_TO_TRADE:
                    print(f"{ts()} | Analizando {symbol}...")
                    
                    # d. Obtener datos
                    df = fetch_ohlcv_df(exchange, symbol=symbol, timeframe=TIMEFRAME, limit=SMA_TREND + 50)
                    if df.empty or len(df) < SMA_TREND:
                        print(f"{ts()} | Datos insuficientes para {symbol}. Omitiendo y continuando con el siguiente.")
                        continue
                        
                    df = build_features(df)
                    current_close = df['close'].iloc[-1]

                    # e. Calcular señal de entrada
                    signal = get_combined_signal(df, MIN_VOLATILITY, MAX_VOLATILITY)
                    print(f"{ts()} | Señal para {symbol}: {signal}")

                    if signal != 'NEUTRAL':
                        # f. Obtener capital disponible
                        try:
                            balance = exchange.fetch_balance()
                            free_balance = balance['free']['USDT']
                            print(f"{ts()} | Capital disponible: {free_balance:.2f} USDT")
                        except Exception as e:
                            print(f"{ts()} | Error al obtener el balance: {e}. Omitiendo...")
                            continue
                        
                        # g. Calcular tamaño de la posición
                        current_atr = df['ATR'].iloc[-1]
                        stop_loss_price = 0
                        if signal == 'BUY':
                            stop_loss_price = current_close - (current_atr * ATR_K)
                            if stop_loss_price <= 0:
                                print(f"{ts()} | Stop loss no válido para {symbol}. Omitiendo...")
                                continue
                            stop_loss_usd = current_close - stop_loss_price
                        else: # SELL
                            stop_loss_price = current_close + (current_atr * ATR_K)
                            stop_loss_usd = stop_loss_price - current_close
                            
                        position_size = calculate_position_size(free_balance, stop_loss_usd)
                        if position_size <= 0:
                            print(f"{ts()} | El tamaño de la posición es cero para {symbol}. Omitiendo...")
                            continue
                        
                        # h. Ejecutar la orden de entrada
                        order_side = 'buy' if signal == 'BUY' else 'sell'
                        order = execute_trade(exchange, symbol, order_side, position_size, current_close)
                        
                        if order:
                            # i. Guardar el estado de la posición y salir del bucle de escaneo
                            trailing_stop = 0
                            if signal == 'BUY':
                                trailing_stop = current_close - (current_atr * TRAIL_R_MULTIPLE)
                            else:
                                trailing_stop = current_close + (current_atr * TRAIL_R_MULTIPLE)
                            
                            position = {
                                'symbol': symbol,
                                'side': signal,
                                'entry_price': current_close,
                                'size': position_size,
                                'trailing_stop': trailing_stop
                            }
                            print(f"{ts()} | Posición abierta. Tamaño: {position_size:.4f}, Stop: {stop_loss_price:.2f}, Trailing Stop: {trailing_stop:.2f}")
                            break # Sale del bucle for para empezar a gestionar la posición

            # j. Esperar el próximo ciclo
            print(f"\n{ts()} | Esperando para el próximo ciclo...")
            time.sleep(LOOP_SLEEP_SEC)

        except KeyboardInterrupt:
            print(f"\n{ts()} | Bot detenido por el usuario.")
            if position:
                print(f"{ts()} | Cerrando posición abierta...")
                side, _, size, _ = position.values()
                close_position(exchange, position['symbol'], side, size)
            break
        except Exception as e:
            print(f"{ts()} | Error inesperado: {e}. Reiniciando el bucle en 60 segundos.")
            time.sleep(60)

if __name__ == "__main__":
    main_execution_loop()
