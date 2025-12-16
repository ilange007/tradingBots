import os
import sys
import time
import pandas as pd
import numpy as np
import ccxt
from datetime import datetime
from dotenv import load_dotenv

# ----------------------------- Utilidades -----------------------------

def ts():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')

# ----------------------------- Configuración -----------------------------

# Asegúrate de tener los 10 parámetros (incluyendo el símbolo, el timeframe y el tiempo del bucle)
if len(sys.argv) < 0:
    print("Error: Se requieren al menos 1 argumentos para el símbolo")#, timeframe, indicadores y el tiempo del bucle.")
    print("Ejemplo de uso: python decision_bot.py BTC/USDT")# 5m 20 10 30 100 8 18 5 10")
    sys.exit(1)

# Lee los parámetros desde el archivo .env
load_dotenv()
TIMEFRAME = os.getenv("TIMEFRAME", "1h")
ATR_PERIOD = int(os.getenv("ATR_PERIOD", "14"))
RSI_PERIOD = int(os.getenv("RSI_PERIOD", "14"))
SMA_FAST = int(os.getenv("SMA_FAST", "30"))
SMA_TREND = int(os.getenv("SMA_TREND", "200"))
MACD_FAST = int(os.getenv("MACD_FAST", "12"))
MACD_SLOW = int(os.getenv("MACD_SLOW", "26"))
MACD_SIGNAL = int(os.getenv("MACD_SIGNAL", "9"))
LOOP_SLEEP_SEC = int(os.getenv("LOOP_SLEEP_SEC", "10"))

# Lee los parámetros desde la línea de comandos
#SYMBOL = sys.argv[1].upper()
SYMBOLS = os.getenv("SYMBOLS")
#TIMEFRAME = sys.argv[2]
#ATR_PERIOD = int(sys.argv[3])
#RSI_PERIOD = int(sys.argv[4])
#SMA_FAST = int(sys.argv[5])
#SMA_TREND = int(sys.argv[6])
#MACD_FAST = int(sys.argv[7])
#MACD_SLOW = int(sys.argv[8])
#MACD_SIGNAL = int(sys.argv[9])
#LOOP_SLEEP_SEC = int(sys.argv[10])


DATA_LIMIT = max(SMA_TREND, MACD_SLOW) + 50
exchange = ccxt.binance()

# ----------------------------- Indicadores y Lógica -----------------------------

def fetch_ohlcv_df(SYMBOL):
    try:
        ohlcv = exchange.fetch_ohlcv(SYMBOL, timeframe=TIMEFRAME, limit=DATA_LIMIT)
        df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        return df
    except Exception as e:
        print(f"Error al obtener datos para {SYMBOL} en {TIMEFRAME}: {e}")
        return pd.DataFrame()

def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period, min_periods=period).mean()

def macd(series: pd.Series, fast: int, slow: int, signal: int):
    exp1 = series.ewm(span=fast, adjust=False).mean()
    exp2 = series.ewm(span=slow, adjust=False).mean()
    macd_line = exp1 - exp2
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - signal_line
    return macd_line, signal_line, hist

def build_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df['sma_trend'] = sma(df['close'], SMA_TREND)
    df['macd'], df['macd_signal'], df['macd_hist'] = macd(df['close'], MACD_FAST, MACD_SLOW, MACD_SIGNAL)
    return df

def get_sma_decision(df: pd.DataFrame) -> tuple:
    last = df.iloc[-1]
    if last['close'] > last['sma_trend']:
        return "LONG", 100
    elif last['close'] < last['sma_trend']:
        return "SHORT", 100
    return "NEUTRAL", 0

def get_macd_decision(df: pd.DataFrame) -> tuple:
    macd_line = df['macd']
    signal_line = df['macd_signal']
    if macd_line.iloc[-2] < signal_line.iloc[-2] and macd_line.iloc[-1] > signal_line.iloc[-1]:
        return "LONG", 100
    elif macd_line.iloc[-2] > signal_line.iloc[-2] and macd_line.iloc[-1] < signal_line.iloc[-1]:
        return "SHORT", 100
    return "NEUTRAL", 0

# ----------------------------- Bucle Principal -----------------------------

def main_loop():
    print(f"\n{ts()} | Bot de decisión iniciado para {SYMBOLS} en el timeframe {TIMEFRAME}. Repitiendo cada {LOOP_SLEEP_SEC} segundos.")
    print("Presione Ctrl+C para salir.\n")

    while True:
        try:
            # Lista de símbolos a escanear. Puedes modificarla o usar get_top_trading_symbols()
            symbols_to_scan = [s.strip() for s in SYMBOLS.split(',')]

            if not symbols_to_scan:
                print(f"{ts()} | No se pudieron obtener los símbolos. Reintentando...")
                time.sleep(60)
                continue
                
            for symbol in symbols_to_scan:
                df = fetch_ohlcv_df(symbol)
                if df.empty or len(df) < max(SMA_TREND, MACD_SLOW):
                     print(f"{ts()} | Datos insuficientes, esperando...")
                     time.sleep(LOOP_SLEEP_SEC)
                     continue
                df = build_features(df)
                sma_decision, sma_certainty = get_sma_decision(df)
                macd_decision, macd_certainty = get_macd_decision(df)
                print(f"{ts()} | Símbolo: {symbol} | Timeframe: {TIMEFRAME}")
                print(f"SMA: Decisión: {sma_decision} | Certeza: {sma_certainty}%")
                print(f"MACD: Decisión: {macd_decision} | Certeza: {macd_certainty}%")              
                print("-" * 50)
            
            time.sleep(LOOP_SLEEP_SEC)
        
        except Exception as e:
            print(f"{ts()} | Error inesperado: {e}")
            time.sleep(LOOP_SLEEP_SEC)
            
if __name__ == "__main__":
    try:
        main_loop()
    except KeyboardInterrupt:
        print(f"\n{ts()} | Saliendo del bot.")