import os
import sys
import time
import ccxt
from dotenv import load_dotenv

# Add project root to Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

# Nota: Este script requiere el archivo 'combined_strategy.py' en la misma carpeta.
from utilities.combined_strategy import get_combined_signal
from utilities.send_mail import send_email_notification
from utilities.build_features import build_features, ts, fetch_ohlcv_df

# ----------------------------- Utilidades -----------------------------

def true_bool(v: str, default=False):
    """Convierte una variable de entorno en un booleano."""
    if v is None:
        return default
    return str(v).strip().lower() in ["1", "true", "yes", "y"]

def timeframe_to_seconds(timeframe: str) -> int:
    """Convierte un string de timeframe (ej. '1h', '4h') en segundos."""
    if not timeframe:
        return 300  # Valor por defecto
    
    unit_map = {
        'm': 60,
        'h': 3600,
        'd': 86400,
        'w': 604800,
        'M': 2592000
    }
    
    try:
        if timeframe[-1] in unit_map:
            value = int(timeframe[:-1])
            unit = timeframe[-1]
            return value * unit_map[unit]
    except (ValueError, IndexError):
        pass  # Manejar errores de formato, devolviendo el valor por defecto
    return 300

def calculate_position_size(balance: float, stop_loss_usd: float, risk_per_trade: float) -> float:
    """Calcula el tamaño de la posición en base al riesgo y el stop loss."""
    if stop_loss_usd <= 0:
        return 0
    risk_usd = balance * risk_per_trade
    return risk_usd / stop_loss_usd

# ----------------------------- Configuración ----------------------------

load_dotenv()

EXCHANGE = os.getenv("EXCHANGE", "binanceusdm").lower()
USE_TESTNET = true_bool(os.getenv("USE_TESTNET", "false"))
TIMEFRAME = os.getenv("TIMEFRAME", "4h")
LOOP_SLEEP_SEC = timeframe_to_seconds(TIMEFRAME)

# Parámetros optimizados de la estrategia combinada
MIN_VOLATILITY = float(os.getenv("MIN_VOLATILITY", "1.00"))
MAX_VOLATILITY = float(os.getenv("MAX_VOLATILITY", "1.45"))
ATR_K = float(os.getenv("ATR_K", "2.5"))
TRAIL_R_MULTIPLE = float(os.getenv("TRAIL_R_MULTIPLE", "4.0"))
RISK_PER_TRADE = float(os.getenv("RISK_PCT", "0.01"))
INITIAL_CAPITAL = float(os.getenv("CAPITAL", "1000"))

# Parámetros de indicadores (deben ser los mismos que en el optimizer y execution_bot)
ATR_PERIOD = 14
RSI_PERIOD = 14
STOCH_RSI_PERIOD = 14
SMA_FAST = 30
SMA_TREND = 200
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9

# ----------------------------- Funciones de Criptomonedas -----------------------------

def check_signal_and_alert(exchange, symbol: str) -> bool:
    """Verifica si hay una señal de trading y envía una alerta si la encuentra."""
    print(f"{ts()} | Analizando {symbol}...")
    
    # 1. Obtener los datos del mercado y calcular indicadores
    df = fetch_ohlcv_df(exchange, symbol=symbol, timeframe=TIMEFRAME, limit=250)
    if df.empty or len(df) < SMA_TREND + 50:
        print(f"{ts()} | Datos insuficientes para {symbol}.")
        return False
        
    df = build_features(df)
    
    # 2. Obtener la señal de la estrategia combinada
    signal, score = get_combined_signal(df, MIN_VOLATILITY, MAX_VOLATILITY)
    print(f"{ts()} | Señal para {symbol}: {signal} {score}/4")

    if signal != 'NEUTRAL':
        # 3. Calcular los parámetros de la operación
        current_close = df['close'].iloc[-1]
        current_atr = df['ATR'].iloc[-1]
        
        stop_loss_price = 0
        trailing_stop_price = 0
        if signal == 'BUY':
            stop_loss_price = current_close - (current_atr * ATR_K)
            trailing_stop_price = current_close - (current_atr * TRAIL_R_MULTIPLE)
            stop_loss_usd = current_close - stop_loss_price
        else: # SELL
            stop_loss_price = current_close + (current_atr * ATR_K)
            trailing_stop_price = current_close + (current_atr * TRAIL_R_MULTIPLE)
            stop_loss_usd = stop_loss_price - current_close

        position_size_usd = calculate_position_size(INITIAL_CAPITAL, stop_loss_usd, RISK_PER_TRADE)
        
        # 4. Enviar notificación por correo electrónico
        subject = f"Alerta de Trading: Señal '{signal}' para {symbol}"
        body = (
            f"El bot de trading ha detectado una señal de '{signal}' para {symbol}.\n\n"
            f"--- Detalles de la Operación ---\n"
            f"Símbolo: {symbol}\n"
            f"Timeframe: {TIMEFRAME}\n"
            f"Señal: {signal}\n"
            f"Puntuación de la señal: {score}/4\n"
            f"Capital inicial: ${INITIAL_CAPITAL:.2f}\n"
            f"Cantidad a comprar: ${position_size_usd:.2f} (en USDT)\n"
            f"ATR: {current_atr:.4f}\n"
            f"Stop Loss: {stop_loss_price:.4f}\n"
            f"Precio de Activación (Trailing Stop): {trailing_stop_price:.4f}\n"
            f"Porcentaje de callback recomendado: {TRAIL_R_MULTIPLE}x ATR\n\n"
            "---------------------------------------\n"
            "Este es un bot de trading automatizado. Por favor, realiza tu propia investigación antes de operar."
        )
        
        send_email_notification(subject, body)
        return False #Esto era TRUE lo cambié para que se revisen todos los símbolos
    
    return False

def main_loop():
    """Bucle principal que busca las señales."""
    print(f"\n{ts()} | Bot de notificaciones iniciado. Escaneando cada {LOOP_SLEEP_SEC} segundos ({round(LOOP_SLEEP_SEC/60, 2)} minutos).")
    exchange = getattr(ccxt, EXCHANGE)({\
        'options': {'defaultType': 'future'},\
        'enableRateLimit': True,\
    })
    
    if USE_TESTNET:
        exchange.set_sandbox_mode(True)
    
    alert_sent = False
    while not alert_sent:
        try:
            # Lista de símbolos a escanear. Puedes modificarla o usar get_top_trading_symbols()
            symbols_to_scan = [s.strip() for s in os.getenv("SYMBOLS", "BTC/USDT").split(',')]

            if not symbols_to_scan:
                print(f"{ts()} | No se pudieron obtener los símbolos. Reintentando...")
                time.sleep(60)
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
            print(f"{ts()} | Error inesperado: {e}. Reiniciando en 60 segundos.")
            time.sleep(60)

if __name__ == "__main__":
    main_loop()
