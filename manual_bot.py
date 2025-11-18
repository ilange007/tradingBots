import os
import sys
import time
import csv
from datetime import datetime
import pandas as pd
import ccxt
from dotenv import load_dotenv
import keyboard
import numpy as np

# ----------------------------- Utilidades -----------------------------

def ts():
    return datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')

def true_bool(v: str, default=False):
    if v is None:
        return default
    return str(v).strip().lower() in ["1", "true", "yes", "y"]

# ----------------------------- Configuración -----------------------------

load_dotenv()

# El primer argumento es la dirección de la posición ('long' o 'short')
if len(sys.argv) < 3:
    print("Error: Se requiere un argumento para la posición (long/short) y otro para el Symbol")
    print("Ejemplo de uso: python manual_bot_v2.py long BNB")
    sys.exit(1)

POSITION_SIDE_ARG = sys.argv[1].lower()
if POSITION_SIDE_ARG not in ['long', 'short']:
    print("Error: El argumento de posición debe ser 'long' o 'short'.")
    sys.exit(1)

EXCHANGE_ID = os.getenv("EXCHANGE", "binanceusdm").lower()
API_KEY = os.getenv("API_KEY", "")
API_SECRET = os.getenv("API_SECRET", "")
USE_TESTNET = true_bool(os.getenv("USE_TESTNET", "false"))
PAPER_TRADE = true_bool(os.getenv("PAPER_TRADE", "true"))

BASE = os.getenv("BASE_SYMBOL", "BTC").upper()
BASE = sys.argv[2] #Sobre escribo BASE de .env y la tomo como argumento
QUOTE = os.getenv("QUOTE_SYMBOL", "USDT").upper()
SYMBOL = f"{BASE}{QUOTE}"
TIMEFRAME = os.getenv("TIMEFRAME", "15m")
CAPITAL = float(os.getenv("CAPITAL", "1000"))
RISK_PCT = float(os.getenv("RISK_PCT", "0.01"))
LEVERAGE = int(os.getenv("LEVERAGE", "10"))
MIN_NOTIONAL = float(os.getenv("MIN_NOTIONAL", "10"))

# Parámetros para TP/SL
ATR_PERIOD = int(os.getenv("ATR_PERIOD", "14"))
ATR_K = float(os.getenv("ATR_K", "1.5"))
TP_R_MULTIPLE = float(os.getenv("TP_R_MULTIPLE", "2.0"))

# Uso de TP/SL fijos por porcentaje si están en .env
TP_PCT = float(os.getenv("TP_PCT", "0"))
SL_PCT = float(os.getenv("SL_PCT", "0"))

DATA_LIMIT = ATR_PERIOD + 10
LOOP_SLEEP_SEC = 10

LOG_FILE = f"logs_{BASE}{QUOTE}_{TIMEFRAME}.csv"
os.makedirs(".", exist_ok=True)

# ----------------------------- Exchange -----------------------------

def make_exchange():
    params = {
        'apiKey': API_KEY,
        'secret': API_SECRET,
        'enableRateLimit': True,
    }
    if EXCHANGE_ID == 'binanceusdm' and USE_TESTNET:
        params['options'] = {
            'defaultType': 'future',
            'urls': {
                'api': 'https://testnet.binancefuture.com/fapi/v1'
            }
        }
    
    ex = getattr(ccxt, EXCHANGE_ID)(params)
    
    if not PAPER_TRADE:
        try:
            ex.set_leverage(LEVERAGE, SYMBOL)
            ex.set_margin_mode('ISOLATED', SYMBOL)
            print(f"{ts()} | Apalancamiento establecido en {LEVERAGE} y modo de margen en ISOLATED")
        except Exception as e:
            print(f"{ts()} | ERROR al configurar apalancamiento: {e}")
            
    return ex

exchange = make_exchange()

# ----------------------------- Data & Indicadores -----------------------------

def fetch_ohlcv_df(symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
    ohlcv = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    df = pd.DataFrame(ohlcv, columns=["timestamp","open","high","low","close","volume"])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    return df

def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high = df['high']
    low = df['low']
    close = df['close']
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs()
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()

# ----------------------------- Lógica de Trading -----------------------------

class Position:
    def __init__(self):
        self.side = None
        self.entry = None
        self.size = 0.0
        self.stop = None
        self.tp = None

position = Position()

# Variable para la señal de cierre manual
manual_close_signal = False

def set_manual_close_signal():
    global manual_close_signal
    manual_close_signal = True
    print(f"\n{ts()} | ¡Señal de cierre manual recibida!")
    
keyboard.add_hotkey('x', set_manual_close_signal)

def compute_position_size(entry: float, stop: float, capital: float, risk_pct: float, leverage: int) -> float:
    risk_amount = capital * risk_pct
    r_value = abs(entry - stop)
    if r_value <= 0: return 0
    size_base = (risk_amount / r_value)
    return size_base

def maybe_open_position(df: pd.DataFrame, capital: float, side: str):
    global position
    if position.side is not None:
        print(f"{ts()} | Ya existe una posición abierta. No se puede abrir una nueva.")
        return

    last = df.iloc[-1]
    entry = float(last['close'])

    if TP_PCT > 0 and SL_PCT > 0:
        # Usar porcentajes fijos de .env
        if side == 'long':
            stop = entry * (1 - SL_PCT)
            tp = entry * (1 + TP_PCT)
        else: # short
            stop = entry * (1 + SL_PCT)
            tp = entry * (1 - TP_PCT)
        r_value = abs(entry - stop)
    else:
        # Calcular TP/SL basados en ATR
        if side == 'long':
            stop = entry - ATR_K * last['atr']
            r_value = entry - stop
            tp = entry + TP_R_MULTIPLE * r_value
        else: # short
            stop = entry + ATR_K * last['atr']
            r_value = stop - entry
            tp = entry - TP_R_MULTIPLE * r_value

    size = compute_position_size(entry, stop, capital, RISK_PCT, LEVERAGE)
    notional = size * entry
    if notional < MIN_NOTIONAL:
        print(f"{ts()} | Señal {side.upper()} ignorada: notional {notional:.2f} < mínimo {MIN_NOTIONAL}")
        return

    if PAPER_TRADE:
        position.side = side
        position.entry = entry
        position.size = size
        position.stop = stop
        position.tp = tp
        print(f"{ts()} | PAPER OPEN {side.upper()} @ {entry:.2f} size={size:.6f} stop={stop:.2f} tp={tp:.2f}")
    else:
        try:
            order_side = 'buy' if side == 'long' else 'sell'
            position_side = 'LONG' if side == 'long' else 'SHORT'
            order = exchange.create_order(SYMBOL, 'market', order_side, size, params={'positionSide': position_side})
            fill_price = order['average'] or entry
            position.side = side
            position.entry = fill_price
            position.size = size
            if side == 'long':
                position.stop = fill_price - ATR_K * last['atr']
                position.tp = fill_price + TP_R_MULTIPLE * abs(fill_price - position.stop)
            else:
                position.stop = fill_price + ATR_K * last['atr']
                position.tp = fill_price - TP_R_MULTIPLE * abs(fill_price - position.stop)
            print(f"{ts()} | OPEN {side.upper()} REAL @ {fill_price:.2f} size={size:.6f} stop={position.stop:.2f} tp={position.tp:.2f}")
        except Exception as e:
            print(f"{ts()} | ERROR OPEN {side.upper()} ORDER: {e}")

def maybe_manage_position(df: pd.DataFrame):
    global position, manual_close_signal
    if position.side is None:
        return
    
    price = float(df.iloc[-1]['close'])
    exit_reason = None
    
    # Cierre manual prioritario
    if manual_close_signal:
        exit_reason = 'manual'
        manual_close_signal = False
    elif position.side == 'long':
        if price <= position.stop:
            exit_reason = 'stop'
        elif price >= position.tp:
            exit_reason = 'tp'
    elif position.side == 'short':
        if price >= position.stop:
            exit_reason = 'stop'
        elif price <= position.tp:
            exit_reason = 'tp'

    if exit_reason:
        pnl_quote = 0
        if position.side == 'long':
            pnl_quote = (price - position.entry) * position.size
        elif position.side == 'short':
            pnl_quote = (position.entry - price) * position.size
            
        if PAPER_TRADE:
            print(f"{ts()} | PAPER CLOSE {position.side.upper()} @ {price:.2f} reason={exit_reason} PnL={pnl_quote:.2f} {QUOTE}")
        else:
            try:
                order_side = 'sell' if position.side == 'long' else 'buy'
                position_side = 'LONG' if position.side == 'long' else 'SHORT'
                order = exchange.create_order(SYMBOL, 'market', order_side, position.size, params={'positionSide': position_side})
                fill_price = order['average'] or price
                if position.side == 'long':
                    pnl_quote = (fill_price - position.entry) * position.size
                else:
                    pnl_quote = (position.entry - fill_price) * position.size
                print(f"{ts()} | CLOSE {position.side.upper()} REAL @ {fill_price:.2f} reason={exit_reason} PnL={pnl_quote:.2f} {QUOTE}")
            except Exception as e:
                print(f"{ts()} | ERROR CLOSE {position.side.upper()} ORDER: {e}")
                return
                
        log_trade(position.entry, price, position.size, position.stop, position.tp, exit_reason, pnl_quote)
        position.__init__()

def ensure_log_header(path):
    if not os.path.exists(path):
        with open(path, 'w', newline='', encoding='utf-8') as f:
            w = csv.writer(f)
            w.writerow(["timestamp","symbol","timeframe","action","price","size_base","stop","tp","reason","pnl_quote"])
            
def log_trade(entry, exit_price, size, stop, tp, reason, pnl_quote):
    ensure_log_header(LOG_FILE)
    with open(LOG_FILE, 'a', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow([ts(), SYMBOL, TIMEFRAME, "CLOSE", f"{exit_price:.4f}", f"{size:.6f}", f"{stop:.4f}", f"{tp:.4f}", reason, f"{pnl_quote:.2f}"])

# ----------------------------- Bucle Principal -----------------------------

def main_loop():
    global position
    
    print(f"{ts()} | Bot manual iniciado. SYMBOL={SYMBOL} TF={TIMEFRAME}")
    print(f"Posición de entrada configurada: {POSITION_SIDE_ARG.upper()}.")
    print("Presione 'x' para cerrar posición o Ctrl+C para salir.")
    
    # Intento de apertura de posición al inicio
    df = fetch_ohlcv_df(SYMBOL, TIMEFRAME, DATA_LIMIT)
    if df.empty or len(df) <= ATR_PERIOD:
        print(f"{ts()} | Datos insuficientes, no se puede abrir posición.")
    else:
        df['atr'] = atr(df, ATR_PERIOD)
        maybe_open_position(df, CAPITAL, POSITION_SIDE_ARG)
    
    if not PAPER_TRADE:
        try:
            positions = exchange.fetch_positions([SYMBOL])
            if positions:
                active_position = None
                for p in positions:
                    if float(p.get('info', {}).get('positionAmt')) != 0:
                        active_position = p
                        break

                if active_position:
                    position.side = 'long' if float(active_position['contracts']) > 0 else 'short'
                    position.entry = float(active_position['entryPrice'])
                    position.size = abs(float(active_position['contracts']))
                    print(f"{ts()} | Posición encontrada en la API: {position.side.upper()} @ {position.entry:.2f}")

                    df = fetch_ohlcv_df(SYMBOL, TIMEFRAME, ATR_PERIOD)
                    if not df.empty:
                        last = df.iloc[-1]
                        if TP_PCT > 0 and SL_PCT > 0:
                            if position.side == 'long':
                                position.stop = position.entry * (1 - SL_PCT)
                                position.tp = position.entry * (1 + TP_PCT)
                            else: # short
                                position.stop = position.entry * (1 + SL_PCT)
                                position.tp = position.entry * (1 - TP_PCT)
                        else:
                            df['atr'] = atr(df, ATR_PERIOD)
                            if position.side == 'long':
                                position.stop = position.entry - ATR_K * last['atr']
                                position.r_value = position.entry - position.stop
                                position.tp = position.entry + TP_R_MULTIPLE * position.r_value
                            else: # short
                                position.stop = position.entry + ATR_K * last['atr']
                                position.r_value = position.stop - position.entry
                                position.tp = position.entry - TP_R_MULTIPLE * position.r_value
                        print(f"{ts()} | Stop y TP recalculados: Stop={position.stop:.2f}, TP={position.tp:.2f}")
                else:
                    print(f"{ts()} | No se encontraron posiciones abiertas en la API.")
            else:
                print(f"{ts()} | No se encontraron posiciones abiertas en la API.")
        except Exception as e:
            print(f"{ts()} | ERROR al verificar posiciones en la API: {e}")

    # while True: eliminando bucle
        try:
            df = fetch_ohlcv_df(SYMBOL, TIMEFRAME, DATA_LIMIT)
            if df.empty or len(df) <= ATR_PERIOD:
                print(f"{ts()} | Esperando más datos... (necesitas al menos {ATR_PERIOD+1} velas)")
                time.sleep(LOOP_SLEEP_SEC)
                #continue eliminando bucle
                
            df['atr'] = atr(df, ATR_PERIOD)
            
            # Gestiona la posición abierta (si la hay)
            if position.side is not None:
                maybe_manage_position(df)

            time.sleep(LOOP_SLEEP_SEC)
        
        except ccxt.RateLimitExceeded as e:
            print(f"{ts()} | Rate limit excedido. Esperando...")
            time.sleep(5)
        except Exception as e:
            print(f"{ts()} | ERROR LOOP: {e}")
            time.sleep(LOOP_SLEEP_SEC)
        finally:
            pass # El bucle ya no espera una tecla para operar.

if __name__ == "__main__":
    try:
        main_loop()
    except KeyboardInterrupt:
        print(f"\n{ts()} | Bot detenido por el usuario.")