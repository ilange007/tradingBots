import ccxt
import os
import time
from dotenv import load_dotenv
import pathlib

# Obtener la ruta del archivo .env
ruta_dotenv = pathlib.Path(__file__).parent.parent.parent / '.env'

# Cargar el archivo .env
load_dotenv(ruta_dotenv)

exchange = ccxt.binance({
    'apiKey': os.getenv('API_KEY'),
    'secret': os.getenv('API_SECRET'),
    'enableRateLimit': True,
    'options': {'defaultType': 'future'}
})

# Si usas Testnet, descomenta esto:
# exchange.set_sandbox_mode(True) 

def ejecutar_ciclo_simple(symbol, lado, cantidad_monedas):
    """
    symbol: 'BTC/USDT'
    lado: 'buy' (Long) o 'sell' (Short)
    cantidad_monedas: Cantidad exacta del activo (ej. 0.001 BTC)
    """
    
    # ---------------------------------------------------------
    # 1. ABRIR POSICIÓN
    # ---------------------------------------------------------
    print(f"1️⃣  Abriendo {lado.upper()} en {symbol}...")
    
    # Una orden normal de mercado abre la posición
    orden_abrir = exchange.create_market_order(symbol, lado, cantidad_monedas)
    print(f"   ✅ Orden de apertura ejecutada: {orden_abrir['id']}")
    
    # --- Simulación de tiempo (esperamos 5 segundos antes de cerrar) ---
    print("   ⏳ Esperando 5 segundos con la posición abierta...")
    time.sleep(5)
    
    # ---------------------------------------------------------
    # 2. CERRAR POSICIÓN (La parte que pediste)
    # ---------------------------------------------------------
    # Lógica: Si abriste con 'buy', cierras con 'sell'. Y viceversa.
    lado_cierre = 'sell' if lado == 'buy' else 'buy'
    
    print(f"2️⃣  Enviando orden para CERRAR ({lado_cierre.upper()})...")

    # AQUÍ ESTÁ LA CLAVE: 'reduceOnly': True
    # Esto le dice a Binance: "Esta orden es solo para reducir/cerrar. 
    # Si la cantidad es mayor a mi posición, corrígela automáticamente y no abras un short nuevo".
    params = {'reduceOnly': True}
    
    orden_cerrar = exchange.create_market_order(symbol, lado_cierre, cantidad_monedas, params=params)
    
    print(f"   ✅ Orden de cierre ejecutada: {orden_cerrar['id']}")
    print("🏁 Ciclo terminado.")

# --- EJEMPLO DE USO ---
# Abrir un Long de 0.01 SOL y cerrarlo a los 5 segundos
ejecutar_ciclo_simple('SOL/USDT', 'long', 0.1)