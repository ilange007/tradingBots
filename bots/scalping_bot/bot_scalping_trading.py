import os
import time
import math
import csv
from datetime import datetime
import numpy as np
import pandas as pd
import ccxt
from dotenv import load_dotenv
import sys
import keyboard

# ----------------------------- Utilidades -----------------------------

def ts():
    return datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')

def true_bool(v: str, default=False):
    if v is None:
        return default
    return str(v).strip().lower() in ["1", "true", "yes", "y"]

# ----------------------------- Config -----------------------------

load_dotenv()

EXCHANGE_ID = os.getenv("EXCHANGE", "binanceusdm").lower()
API_KEY = os.getenv("API_KEY", "")
API_SECRET = os.getenv("API_SECRET", "")
USE_TESTNET = true_bool(os.getenv("USE_TESTNET", "true"))
PAPER_TRADE = true_bool(os.getenv("PAPER_TRADE", "true"))

BASE = os.getenv("BASE_SYMBOL", "BTC").upper()
QUOTE = os.getenv("QUOTE_SYMBOL", "USDT").upper()
SYMBOL = f"{BASE}{QUOTE}"
TIMEFRAME = os.getenv("TIMEFRAME", "1h")
CAPITAL = float(os.getenv("CAPITAL", "5000"))
RISK_PCT = float(os.getenv("RISK_PCT", "0.01"))

# Lectura de parámetros desde línea de comandos o .env
if len(sys.argv) > 6:
    ATR_PERIOD = int(sys.argv[1])
    RSI_PERIOD = int(sys.argv[2])
    SMA_FAST = int(sys.argv[3])
    SMA_TREND = int(sys.argv[4])
    MACD_FAST = int(sys.argv[5])
    MACD_SLOW = int(sys.argv[6])
    MACD_SIGNAL = int(sys.argv[7])
    print(f"{ts()} | Parámetros leídos de la línea de comandos: ATR={ATR_PERIOD}, RSI={RSI_PERIOD}, SMA_FAST={SMA_FAST}, SMA_TREND={SMA_TREND}, MACD_FAST={MACD_FAST}, MACD_SLOW={MACD_SLOW}, MACD_SIGNAL={MACD_SIGNAL}")
else:
    ATR_PERIOD = int(os.getenv("ATR_PERIOD", "14"))
    RSI_PERIOD = int(os.getenv("RSI_PERIOD", "14"))
    SMA_FAST = int(os.getenv("SMA_FAST", "20"))
    SMA_TREND = int(os.getenv("SMA_TREND", "200"))
    MACD_FAST = int(os.getenv("MACD_FAST", "12"))
    MACD_SLOW = int(os.getenv("MACD_SLOW", "26"))
    MACD_SIGNAL = int(os.getenv("MACD_SIGNAL", "9"))
    print(f"{ts()} | Usando parámetros del archivo .env o por defecto.")

TP_R_MULTIPLE = float(os.getenv("TP_R_MULTIPLE", "2.0"))
TRAIL_R_MULTIPLE = float(os.getenv("TRAIL_R_MULTIPLE", "1.0"))
MIN_NOTIONAL = float(os.getenv("MIN_NOTIONAL", "10"))
LEVERAGE = int(os.getenv("LEVERAGE", "5"))
ATR_K = float(os.getenv("ATR_K", "1.5"))

DATA_LIMIT = max(SMA_TREND, MACD_SLOW) + 50
LOOP_SLEEP_SEC = 10
CLOSE_FILE = 'close_order.txt'

LOG_FILE = f"logs_{BASE}{QUOTE}_{TIMEFRAME}.csv"
STATE_FILE = f"state_{BASE}{QUOTE}_{TIMEFRAME}.csv"

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

def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period, min_periods=period).mean()

def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    up = np.where(delta > 0, delta, 0.0)
    down = np.where(delta < 0, -delta, 0.0)
    roll_up = pd.Series(up).rolling(period).mean()
    roll_down = pd.Series(down).rolling(period).mean()
    rs = roll_up / (roll_down + 1e-12)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    rsi.index = series.index
    return rsi

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

def macd(series: pd.Series, fast: int, slow: int, signal: int):
    exp1 = series.ewm(span=fast, adjust=False).mean()
    exp2 = series.ewm(span=slow, adjust=False).mean()
    macd_line = exp1 - exp2
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - signal_line
    return macd_line, signal_line, hist

# ----------------------------- Estrategia de Trading (Long & Short) -----------------------------

def build_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df['sma_fast'] = sma(df['close'], SMA_FAST)
    df['sma_trend'] = sma(df['close'], SMA_TREND)
    df['rsi'] = rsi(df['close'], RSI_PERIOD)
    df['atr'] = atr(df, ATR_PERIOD)
    df['macd'], df['macd_signal'], df['macd_hist'] = macd(df['close'], MACD_FAST, MACD_SLOW, MACD_SIGNAL)
    return df

class Position:
    def __init__(self):
        self.side = None
        self.entry = None
        self.size = 0.0
        self.stop = None
        self.tp = None
        self.r_value = None
        self.highest = None
        self.lowest = None

position = Position()

TRADE_MODES = ['long', 'short', 'automatic_sma', 'automatic_macd']
trade_mode = None

def set_manual_close_signal():
    global manual_close_signal
    manual_close_signal = True
    print(f"\n{ts()} | ¡Señal de cierre manual recibida! La posición se cerrará en la siguiente iteración.")

def toggle_trade_mode():
    global trade_mode
    if trade_mode is None:
        trade_mode = TRADE_MODES[0]
    else:
        current_index = TRADE_MODES.index(trade_mode)
        next_index = (current_index + 1) % len(TRADE_MODES)
        trade_mode = TRADE_MODES[next_index]
    
    print(f"\n{ts()} | ¡Modo de operación cambiado a: {trade_mode.replace('_', ' ').upper()}!")
    
keyboard.add_hotkey('t', toggle_trade_mode)
keyboard.add_hotkey('c', set_manual_close_signal)

def check_entry_long(df: pd.DataFrame, mode: str) -> float:
    last = df.iloc[-1]
    if np.isnan(last['atr']):
        return None
    
    if mode == 'long':
        return float(last['close'])
    elif mode == 'automatic_sma':
        if last['close'] > last['sma_trend']:
            return float(last['close'])
    elif mode == 'automatic_macd':
        # MACD Line crosses above Signal Line
        if df['macd'].iloc[-2] < df['macd_signal'].iloc[-2] and df['macd'].iloc[-1] > df['macd_signal'].iloc[-1]:
            return float(last['close'])
    return None

def check_entry_short(df: pd.DataFrame, mode: str) -> float:
    last = df.iloc[-1]
    if np.isnan(last['atr']):
        return None
    
    if mode == 'short':
        return float(last['close'])
    elif mode == 'automatic_sma':
        if last['close'] < last['sma_trend']:
            return float(last['close'])
    elif mode == 'automatic_macd':
        # MACD Line crosses below Signal Line
        if df['macd'].iloc[-2] > df['macd_signal'].iloc[-2] and df['macd'].iloc[-1] < df['macd_signal'].iloc[-1]:
            return float(last['close'])
    return None

def compute_position_size(entry: float, stop: float, capital: float, risk_pct: float, leverage: int) -> float:
    risk_amount = capital * risk_pct
    r_value = abs(entry - stop)
    if r_value <= 0: return 0
    size_base = (risk_amount / r_value)
    return size_base

def maybe_open_position(df: pd.DataFrame, capital: float):
    global position, trade_mode
    if position.side is not None:
        return

    entry = None
    side = None
    
    # Logic to decide which side to check based on the current trade mode
    if trade_mode == 'long':
        entry = check_entry_long(df, trade_mode)
        if entry is not None:
            side = 'long'
    elif trade_mode == 'short':
        entry = check_entry_short(df, trade_mode)
        if entry is not None:
            side = 'short'
    elif trade_mode == 'automatic_sma':
        if df.iloc[-1]['close'] > df.iloc[-1]['sma_trend']:
            entry = check_entry_long(df, trade_mode)
            if entry is not None:
                side = 'long'
        elif df.iloc[-1]['close'] < df.iloc[-1]['sma_trend']:
            entry = check_entry_short(df, trade_mode)
            if entry is not None:
                side = 'short'
    elif trade_mode == 'automatic_macd':
        entry = check_entry_long(df, trade_mode)
        if entry is not None:
            side = 'long'
        else:
            entry = check_entry_short(df, trade_mode)
            if entry is not None:
                side = 'short'

    if entry is None or side is None:
        return

    last = df.iloc[-1]
    
    if side == 'long':
        stop = entry - ATR_K * last['atr']
        r_value = entry - stop
        size = compute_position_size(entry, stop, capital, RISK_PCT, LEVERAGE)
        notional = size * entry
        if notional < MIN_NOTIONAL:
            print(f"{ts()} | Señal LONG ignorada: notional {notional:.2f} < mínimo {MIN_NOTIONAL}")
            return
        
        if PAPER_TRADE:
            position.side = side
            position.entry = entry
            position.size = size
            position.stop = stop
            position.r_value = r_value
            position.tp = entry + TP_R_MULTIPLE * r_value
            position.highest = entry
            print(f"{ts()} | PAPER OPEN LONG @ {entry:.2f} size={size:.6f} stop={stop:.2f} tp={position.tp:.2f}")
        else:
            try:
                amount = float(f"{size:.6f}")
                order = exchange.create_order(SYMBOL, 'market', 'buy', amount, params={'positionSide': 'LONG'})
                fill_price = order['average'] or entry
                position.side = side
                position.entry = fill_price
                position.size = amount
                position.stop = fill_price - ATR_K * last['atr']
                position.r_value = fill_price - position.stop
                position.tp = fill_price + TP_R_MULTIPLE * position.r_value
                position.highest = fill_price
                print(f"{ts()} | OPEN LONG REAL @ {fill_price:.2f} size={amount}")
            except Exception as e:
                print(f"{ts()} | ERROR OPEN LONG ORDER: {e}")
    
    elif side == 'short':
        stop = entry + ATR_K * last['atr']
        r_value = stop - entry
        size = compute_position_size(entry, stop, capital, RISK_PCT, LEVERAGE)
        notional = size * entry
        if notional < MIN_NOTIONAL:
            print(f"{ts()} | Señal SHORT ignorada: notional {notional:.2f} < mínimo {MIN_NOTIONAL}")
            return
        
        if PAPER_TRADE:
            position.side = side
            position.entry = entry
            position.size = size
            position.stop = stop
            position.r_value = r_value
            position.tp = entry - TP_R_MULTIPLE * r_value
            position.lowest = entry
            print(f"{ts()} | PAPER OPEN SHORT @ {entry:.2f} size={size:.6f} stop={stop:.2f} tp={position.tp:.2f}")
        else:
            try:
                amount = float(f"{size:.6f}")
                order = exchange.create_order(SYMBOL, 'market', 'sell', amount, params={'positionSide': 'SHORT'})
                fill_price = order['average'] or entry
                position.side = side
                position.entry = fill_price
                position.size = amount
                position.stop = fill_price + ATR_K * last['atr']
                position.r_value = position.stop - fill_price
                position.tp = fill_price - TP_R_MULTIPLE * position.r_value
                position.lowest = fill_price
                print(f"{ts()} | OPEN SHORT REAL @ {fill_price:.2f} size={amount}")
            except Exception as e:
                print(f"{ts()} | ERROR OPEN SHORT ORDER: {e}")


def maybe_manage_position(df: pd.DataFrame):
    global position
    if position.side is None:
        return None
    
    last = df.iloc[-1]
    price = float(last['close'])
    exit_reason = None
    
    if os.path.exists(CLOSE_FILE):
        os.remove(CLOSE_FILE)
        exit_reason = 'manual'
        print(f"{ts()} | Señal de cierre manual detectada. Cerrando posición.")

    if not exit_reason:
        if position.side == 'long':
            if price > (position.highest or price):
                position.highest = price
            if position.highest and position.r_value and TRAIL_R_MULTIPLE > 0:
                trail_stop = position.highest - TRAIL_R_MULTIPLE * position.r_value
                if trail_stop > position.stop:
                    position.stop = trail_stop
            
            if price <= position.stop:
                exit_reason = 'stop'
            elif price >= position.tp:
                exit_reason = 'tp'
            
        elif position.side == 'short':
            if price < (position.lowest or price):
                position.lowest = price
            if position.lowest and position.r_value and TRAIL_R_MULTIPLE > 0:
                trail_stop = position.lowest + TRAIL_R_MULTIPLE * position.r_value
                if trail_stop < position.stop:
                    position.stop = trail_stop
            
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
                amount = float(f"{position.size:.6f}")
                order_side = 'sell' if position.side == 'long' else 'buy'
                position_side = 'LONG' if position.side == 'long' else 'SHORT'
                order = exchange.create_order(SYMBOL, 'market', order_side, amount, params={'positionSide': position_side})
                fill_price = order['average'] or price
                if position.side == 'long':
                    pnl_quote = (fill_price - position.entry) * amount
                else:
                    pnl_quote = (position.entry - fill_price) * amount
                print(f"{ts()} | CLOSE {position.side.upper()} REAL @ {fill_price:.2f} reason={exit_reason} PnL={pnl_quote:.2f} {QUOTE}")
            except Exception as e:
                print(f"{ts()} | ERROR CLOSE {position.side.upper()} ORDER: {e}")
                return None
                
        log_trade(position.entry, price, position.size, position.stop, position.tp, exit_reason, pnl_quote)
        position.__init__()
        return pnl_quote

    return None

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

def read_state():
    if not os.path.exists(STATE_FILE):
        return {"capital": CAPITAL, "trade_mode": TRADE_MODES[0]}
    try:
        df = pd.read_csv(STATE_FILE)
        if len(df) > 0:
            last_row = df.iloc[-1]
            return {
                "capital": float(last_row.get('capital', CAPITAL)),
                "trade_mode": str(last_row.get('trade_mode', TRADE_MODES[0]))
            }
    except Exception as e:
        print(f"Error al leer el estado del archivo, usando valores por defecto: {e}")
    return {"capital": CAPITAL, "trade_mode": TRADE_MODES[0]}

def write_state(capital, trade_mode):
    exists = os.path.exists(STATE_FILE)
    with open(STATE_FILE, 'a', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        if not exists:
            w.writerow(["timestamp","capital","trade_mode"])
        w.writerow([ts(), f"{capital:.2f}", trade_mode])

# ----------------------------- Main Loop -----------------------------

def main_loop():
    global position, trade_mode
    
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
                    df = build_features(df)
                    last = df.iloc[-1]
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

    state = read_state()
    capital = state['capital']
    trade_mode = state.get('trade_mode', TRADE_MODES[0])
    
    print(f"{ts()} | Iniciando bot. SYMBOL={SYMBOL} TF={TIMEFRAME} PAPER={PAPER_TRADE} TESTNET={USE_TESTNET}")
    print(f"{ts()} | Capital de referencia: {capital} {QUOTE}")
    print(f"{ts()} | Modo de operación: {trade_mode.replace('_', ' ').upper()}")
    write_state(capital, trade_mode)

    while True:
        try:
            df = fetch_ohlcv_df(SYMBOL, TIMEFRAME, DATA_LIMIT)
            if len(df) < SMA_TREND or len(df) < MACD_SLOW:
                print(f"{ts()} | Esperando más datos... (necesitas al menos {max(SMA_TREND, MACD_SLOW)} velas)")
                time.sleep(LOOP_SLEEP_SEC)
                continue
            
            df = build_features(df)
            
            maybe_open_position(df, capital)
            
            pnl = maybe_manage_position(df)
            if pnl is not None:
                capital += pnl
                write_state(capital, trade_mode)
                print(f"{ts()} | Capital actualizado (paper): {capital:.2f} {QUOTE}")
                
        except ccxt.RateLimitExceeded as e:
            print(f"{ts()} | Rate limit excedido. Esperando...")
            time.sleep(5)
        except Exception as e:
            print(f"{ts()} | ERROR LOOP: {e}")
            time.sleep(LOOP_SLEEP_SEC)
        finally:
            time.sleep(LOOP_SLEEP_SEC)

if __name__ == "__main__":
    try:
        main_loop()
    except KeyboardInterrupt:
        print(f"\n{ts()} | Bot detenido por el usuario.")