import pandas as pd
import pandas_ta as ta
import os
from dotenv import load_dotenv
import pathlib

# Obtener la ruta del archivo .env
ruta_dotenv = pathlib.Path(__file__).parent.parent / '.env'

# Cargar el archivo .env
load_dotenv(ruta_dotenv)

capital=float(os.getenv('CAPITAL', 10))  # Capital fijo por operación

# Simulación de memoria para Trailing Stop
posiciones_activas = {}

# Parámetros de Estrategia
RSI_SOBREVENTA = 30
RSI_SOBRECOMPRA = 70
SMA_TENDENCIA = 200

def obtener_data_historica(exchange, symbol, timeframe='1h', limite=300):
    """Descarga velas reales de Binance para calcular indicadores"""
    try:
        bars = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limite)
        df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        return df
    except Exception as e:
        print(f"Error descargando data: {e}")
        return None

def consultar_senal_mercado(exchange, symbol):
    """
    Analiza SMA, RSI y MACD en 1H.
    """
    if symbol in posiciones_activas:
        return {'entrar': False}

    print(f"   🧠 [Cerebro]: Calculando indicadores 1H para {symbol}...")
    
    df = obtener_data_historica(exchange, symbol)
    if df is None: return {'entrar': False}

    # --- CÁLCULO DE INDICADORES ---
    # 1. Tendencia (SMA 200)
    df['sma200'] = ta.sma(df['close'], length=SMA_TENDENCIA)
    
    # 2. Momento (RSI 14)
    df['rsi'] = ta.rsi(df['close'], length=14)
    
    # 3. Confirmación (MACD)
    macd = ta.macd(df['close'])
    df = pd.concat([df, macd], axis=1) # Unir columnas MACD
    
    # Tomamos la última vela cerrada (penúltima en la lista) para confirmar señal
    last = df.iloc[-2] 
    current_price = df.iloc[-1]['close']

    # --- LÓGICA DE ENTRADA (ESTRATEGIA SWING) ---
    
    # LONG: Precio sobre SMA200 + RSI cruzando hacia arriba + MACD positivo
    condicion_long = (
        last['close'] > last['sma200'] and     # Tendencia Alcista
        last['rsi'] < 40 and                   # RSI bajo (rebote)
        last['MACDh_12_26_9'] > 0              # MACD Histograma positivo (cruce alcista)
    )

    # SHORT: Precio bajo SMA200 + RSI alto + MACD negativo
    condicion_short = (
        last['close'] < last['sma200'] and     # Tendencia Bajista
        last['rsi'] > 60 and                   # RSI alto (caída)
        last['MACDh_12_26_9'] < 0              # MACD Histograma negativo
    )

    # NOTA: Para pruebas, forzaremos una señal aleatoria a veces si no hay señal real,
    # QUITA ESTO EN PRODUCCIÓN para usar solo señales reales.
    if True: return {'entrar': True, 'lado': 'long', 'cantidad_usdt': capital}

    if condicion_long:
        print(f"   🔥 [SEÑAL]: LONG confirmado en {symbol}. RSI: {last['rsi']:.2f}")
        return {'entrar': True, 'lado': 'long', 'cantidad_usdt': capital} # Margen fijo $2
    
    elif condicion_short:
        print(f"   🔥 [SEÑAL]: SHORT confirmado en {symbol}. RSI: {last['rsi']:.2f}")
        return {'entrar': True, 'lado': 'short', 'cantidad_usdt': capital}

    return {'entrar': False}

def registrar_entrada(symbol, precio_entrada, lado, leverage=1):
    posiciones_activas[symbol] = {
        'entry_price': precio_entrada,
        'highest_pnl': -100, # Iniciamos bajo para asegurar que se actualice
        'side': lado,
        'leverage': leverage # <--- NUEVO: Guardamos el apalancamiento
    }
    print(f"   📝 [Cerebro]: Posición registrada en {symbol} ({lado}) a {precio_entrada} con {leverage}x")

def monitorear_posicion(symbol, precio_actual):
    """
    Trailing Stop Corregido:
    1. Calcula el ROI real usando el apalancamiento.
    2. Se activa si el ROI supera el 5%.
    3. Cierra si el ROI retrocede un 5% desde su punto máximo.
    """
    if symbol not in posiciones_activas: return {'accion': 'NADA'}
    
    data = posiciones_activas[symbol]
    entry = data['entry_price']
    lev = data.get('leverage', 1) # Si no hay dato, asumimos 1x
    
    # 1. Calcular cambio del precio puro
    if entry == 0: return 'NADA' # Evitar división por cero
    
    cambio_precio_pct = (precio_actual - entry) / entry
    
    # Invertir lógica si es SHORT
    if data['side'] == 'short': 
        cambio_precio_pct *= -1
    
    # 2. Calcular ROI Real (Return on Equity)
    # Ejemplo: Precio mueve 1% * 10x Apalancamiento = 10% ROI
    roi_actual = cambio_precio_pct * 100 * lev 
    
    # 3. Actualizar el "High Water Mark" (Máximo ROI visto)
    if roi_actual > data['highest_pnl']:
        data['highest_pnl'] = roi_actual
        # Solo mostramos en consola si es una ganancia relevante
        if roi_actual > 1: 
            print(f"   💰 {symbol} Nuevo Máximo PnL: {roi_actual:.2f}% (Precio: {precio_actual})")

    # -----------------------------------------------------------
    # LÓGICA DE SALIDA (Ajustada a tu requerimiento)
    # -----------------------------------------------------------
    
    UMBRAL_ACTIVACION = 0  # Activar vigilancia al ganar 5%
    UMBRAL_RETROCESO = 0   # Cerrar si devolvemos 5% desde el máximo

    # Si alguna vez tuvimos más de 5% de ganancia...
    if data['highest_pnl'] > UMBRAL_ACTIVACION: 
        retroceso = data['highest_pnl'] - roi_actual
        
        # Y si hemos perdido 5% desde ese pico...
        if retroceso >= UMBRAL_RETROCESO: 
            print(f"   🏃 [Salida Trailing]: Max PnL fue {data['highest_pnl']:.2f}%, Actual es {roi_actual:.2f}%. Retroceso: {retroceso:.2f}%")
            lado = data['side']
            del posiciones_activas[symbol]
            return {'accion': 'CERRAR', 'lado': lado}
            
    # Stop Loss de seguridad (Hard Stop)
    # Si perdemos más del 50% del margen inicial, cerramos.
    if roi_actual < -1: 
         print(f"   💀 [Stop Loss]: PnL crítico de {roi_actual:.2f}%")
         lado = data['side']
         del posiciones_activas[symbol]
         return {'accion': 'CERRAR', 'lado': lado}

    # Debug: mostrar estado actual
    print(f"   📊 {symbol}: ROI {roi_actual:.2f}% | Max {data['highest_pnl']:.2f}% | Estado: MANTENER")
    return {'accion': 'MANTENER', 'lado': data.get('side', 'long')}