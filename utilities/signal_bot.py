import os
import time
import math
from datetime import datetime
import numpy as np
import pandas as pd
import ccxt
import smtplib
from email.mime.text import MIMEText
from dotenv import load_dotenv

# ----------------------------- Utilidades -----------------------------

def ts():
    return datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')

def true_bool(v: str, default=False):
    if v is None:
        return default
    return str(v).strip().lower() in ["1", "true", "yes", "y"]

# ----------------------------- Configuración ----------------------------

load_dotenv()

EXCHANGE_ID = os.getenv("EXCHANGE", "binanceusdm").lower()
API_KEY = os.getenv("API_KEY", "")
API_SECRET = os.getenv("API_SECRET", "")
USE_TESTNET = true_bool(os.getenv("USE_TESTNET", "false"))
RISK_PCT = float(os.getenv("RISK_PCT", "0.01"))
ATR_PERIOD = int(os.getenv("ATR_PERIOD", "14"))
SMA_FAST = int(os.getenv("SMA_FAST", "30"))
SMA_TREND = int(os.getenv("SMA_TREND", "200"))
MACD_FAST = int(os.getenv("MACD_FAST", "12"))
MACD_SLOW = int(os.getenv("MACD_SLOW", "26"))
MACD_SIGNAL = int(os.getenv("MACD_SIGNAL", "9"))
LOOP_SLEEP_SEC = int(os.getenv("LOOP_SLEEP_SEC", "300"))
TP_R_MULTIPLE = float(os.getenv("TP_R_MULTIPLE", "2.0"))
ATR_K = float(os.getenv("ATR_K", "2.0"))
TIMEFRAME = os.getenv("TIMEFRAME", "1h")

# Configuración de correo
EMAIL_SENDER = os.getenv("EMAIL_SENDER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_RECEIVER = os.getenv("EMAIL_RECEIVER")
EMAIL_SMTP_SERVER = os.getenv("EMAIL_SMTP_SERVER")
EMAIL_SMTP_PORT = int(os.getenv("EMAIL_SMTP_PORT"))

# Nueva variable para el número de símbolos a escanear
NUM_SYMBOLS_TO_SCAN = int(os.getenv("NUM_SYMBOLS_TO_SCAN", "10"))
QUOTE_SYMBOL = os.getenv("QUOTE_SYMBOL", "USDT")

# ----------------------------- Funciones de Indicadores ----------------------------

def atr(df, period):
    df['high_low'] = df['high'] - df['low']
    df['high_close'] = np.abs(df['high'] - df['close'].shift())
    df['low_close'] = np.abs(df['low'] - df['close'].shift())
    df['tr'] = df[['high_low', 'high_close', 'low_close']].max(axis=1)
    df['atr'] = df['tr'].rolling(period).mean()
    return df['atr']

def macd(df, fast, slow, signal):
    df['ema_fast'] = df['close'].ewm(span=fast, adjust=False).mean()
    df['ema_slow'] = df['close'].ewm(span=slow, adjust=False).mean()
    df['macd'] = df['ema_fast'] - df['ema_slow']
    df['macd_signal'] = df['macd'].ewm(span=signal, adjust=False).mean()
    df['macd_hist'] = df['macd'] - df['macd_signal']
    return df

def sma(df, period):
    return df['close'].rolling(period).mean()

# ----------------------------- Funciones de Trading -----------------------------

def send_email_alert(symbol, side, entry, sl_price, tp_price):
    try:
        subject = f"Alerta de Trading: Señal de {side.upper()} en {symbol}!"
        body = (
            f"Se ha detectado una señal de doble confirmación.\n\n"
            f"Símbolo: {symbol}\n"
            f"Posición: {side.upper()}\n"
            f"Precio de Entrada: {entry:.2f} USDT\n\n"
            f"--- Parámetros para Binance ---\n"
            f"Precio de Stop Loss (SL): {sl_price:.2f} USDT\n"
            f"Precio de Take Profit (TP): {tp_price:.2f} USDT\n"
            f"Trailing Stop (Callback): 1.0%\n\n"
            f"Recuerda que solo el SL fijo es requerido, el Trailing Stop es opcional para maximizar ganancias."
        )

        msg = MIMEText(body)
        msg['Subject'] = subject
        msg['From'] = EMAIL_SENDER
        msg['To'] = EMAIL_RECEIVER

        with smtplib.SMTP(EMAIL_SMTP_SERVER, EMAIL_SMTP_PORT) as server:
            server.starttls()
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.sendmail(EMAIL_SENDER, EMAIL_RECEIVER, msg.as_string())
        print(f"{ts()} | Alerta de correo enviada para {symbol}.")
    except Exception as e:
        print(f"{ts()} | ERROR al enviar correo: {e}")

def get_top_trading_symbols(exchange, limit=NUM_SYMBOLS_TO_SCAN):
    print(f"{ts()} | Obteniendo los top {limit} símbolos de trading...")
    try:
        # Obtener todos los tickers
        tickers = exchange.fetch_tickers()
        
        # Filtrar por pares USDT y eliminar stablecoins (que no tienen volumen)
        filtered_tickers = {
            symbol: ticker for symbol, ticker in tickers.items()
            if symbol.endswith('/' + QUOTE_SYMBOL) and ticker.get('quoteVolume') is not None
            and symbol not in ['USDT/USDT']
        }
        
        # Convertir a una lista de tuplas (símbolo, volumen)
        volume_data = [(symbol, ticker['quoteVolume']) for symbol, ticker in filtered_tickers.items()]
        
        # Ordenar por volumen en orden descendente y tomar los top N
        sorted_volume_data = sorted(volume_data, key=lambda x: x[1], reverse=True)
        top_symbols = [item[0] for item in sorted_volume_data[:limit]]
        
        return top_symbols
        
    except Exception as e:
        print(f"{ts()} | ERROR al obtener los símbolos: {e}")
        return []

def check_signal_and_alert(exchange, symbol):
    print(f"{ts()} | Analizando {symbol}...")
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, TIMEFRAME, limit=SMA_TREND + 5)
        if not ohlcv:
            print(f"{ts()} | No se encontraron datos para {symbol}.")
            return
        
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        
        # Calcular indicadores
        df['sma_fast'] = sma(df, SMA_FAST)
        df['sma_trend'] = sma(df, SMA_TREND)
        df = macd(df, MACD_FAST, MACD_SLOW, MACD_SIGNAL)
        df['atr'] = atr(df, ATR_PERIOD)
        
        last_row = df.iloc[-1]
        
        sma_long = last_row['sma_fast'] > last_row['sma_trend']
        macd_long = last_row['macd'] > last_row['macd_signal']
        
        sma_short = last_row['sma_fast'] < last_row['sma_trend']
        macd_short = last_row['macd'] < last_row['macd_signal']
        
        atr_value = last_row['atr']
        
        # Doble confirmación y envío de alerta
        if sma_long and macd_long:
            side = 'long'
            entry_price = last_row['close']
            stop_loss = entry_price - (atr_value * ATR_K)
            take_profit = entry_price + (atr_value * (ATR_K * TP_R_MULTIPLE))
            send_email_alert(symbol, side, entry_price, stop_loss, take_profit)
            return True
        elif sma_short and macd_short:
            side = 'short'
            entry_price = last_row['close']
            stop_loss = entry_price + (atr_value * ATR_K)
            take_profit = entry_price - (atr_value * (ATR_K * TP_R_MULTIPLE))
            send_email_alert(symbol, side, entry_price, stop_loss, take_profit)
            return True
        else:
            print(f"{ts()} | No hay señal de doble confirmación en {symbol}.")

    except ccxt.NetworkError as e:
        print(f"{ts()} | ERROR de red en {symbol}: {e}")
    except ccxt.ExchangeError as e:
        print(f"{ts()} | ERROR del exchange en {symbol}: {e}")
    except Exception as e:
        print(f"{ts()} | ERROR inesperado en {symbol}: {e}")
    return False

# ----------------------------- Bucle Principal -----------------------------

def main_loop():
    print(f"{ts()} | Bot de alerta de señales iniciado. Escaneando cada {LOOP_SLEEP_SEC/60} minutos.")
    exchange = getattr(ccxt, EXCHANGE_ID)({
        #'apiKey': API_KEY,
        #'secret': API_SECRET,
        'options': {'defaultType': 'future'},
        'enableRateLimit': True,
    })
    
    if USE_TESTNET:
        exchange.set_sandbox_mode(True)
    
    alert_sent = False
    while not alert_sent:
        try:
            symbols_to_scan = get_top_trading_symbols(exchange) #Revisar para usar luego, de momento lo puse manual
            symbols_to_scan = ['BTC/USDT', 'ETH/USDT', 'BNB/USDT', 'SOL/USDT', 'ADA/USDT', 'XRP/USDT']
            if not symbols_to_scan:
                print(f"{ts()} | No se pudieron obtener los símbolos. Reintentando...")
                time.sleep(60) # Espera un minuto antes de reintentar
                continue
                
            for symbol in symbols_to_scan:
                if check_signal_and_alert(exchange, symbol):
                    alert_sent = True
                    break
            
            if not alert_sent:
                print(f"{ts()} | Esperando para el próximo ciclo...")
                time.sleep(LOOP_SLEEP_SEC)
        
        except KeyboardInterrupt:
            print(f"\n{ts()} | Bot detenido por el usuario.")
            break
        except Exception as e:
            print(f"\n{ts()} | ERROR en el bucle principal: {e}")
            time.sleep(LOOP_SLEEP_SEC)

if __name__ == "__main__":
    main_loop()