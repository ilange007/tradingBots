import sys
import ccxt
import os
import time
from dotenv import load_dotenv
import utilities.cerebro
# Add project root to Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))
# Cargar variables de entorno
load_dotenv()

# Inicialización
exchange = ccxt.binance({
    'apiKey': os.getenv('API_KEY'),
    'secret': os.getenv('API_SECRET'),
    'enableRateLimit': True,
    'options': {'defaultType': 'future'}
})

if os.getenv('USE_TESTNET') == 'true':
    # Binance deprecated sandbox/testnet for futures. Only enable sandbox for spot.
    default_type = exchange.options.get('defaultType', '') if isinstance(exchange.options, dict) else ''
    if default_type.lower() != 'future':
        try:
            exchange.set_sandbox_mode(True)
            print("🧪 Sandbox mode enabled (spot)")
        except Exception as e:
            print(f"❌ No se pudo habilitar sandbox: {e}")
    else:
        print("⚠️ Binance futures testnet/sandbox is not supported anymore.\n" \
              "See https://t.me/ccxt_announcements/92 and consider using demo trading instead.")

def configurar_margen_aislado(symbol):
    """Fuerza el modo ISOLATED para proteger el resto de la cuenta"""
    try:
        # Cambiar a modo Isolated
        exchange.fapiPrivate_post_margintype({
            'symbol': symbol.replace('/', ''),
            'marginType': 'ISOLATED'
        })
        print(f"🛡️ Modo ISOLATED configurado para {symbol}")
    except Exception as e:
        # Si ya es isolated da error, lo ignoramos pero avisamos
        # print(f"Info margen {symbol}: {e}") 
        pass

def configurar_apalancamiento_maximo(symbol):
    try:
        market = exchange.market(symbol)
        max_lev = market['limits']['leverage']['max']
        # ccxt implementations differ in argument order for set_leverage.
        # Try the common (leverage, symbol) first, fall back to (symbol, leverage).
        try:
            exchange.set_leverage(max_lev, symbol)
        except TypeError:
            exchange.set_leverage(symbol, max_lev)
        except Exception:
            try:
                exchange.set_leverage(50, symbol)
                return 50
            except Exception as e:
                print(f"❌ No se pudo configurar apalancamiento: {e}")
                return 50

        return max_lev
    except Exception as e:
        try:
            exchange.set_leverage(50, symbol)
        except Exception as e2:
            print(f"❌ Error fallback apalancamiento: {e2}")
        return 50

def _get_free_usdt_balance():
    """Try several common ccxt balance shapes to find free USDT available for futures.
    Returns a float (0.0 on failure).
    """
    try:
        bal = exchange.fetch_balance()
    except Exception as e:
        print(f"⚠️ No se pudo obtener balance: {e}")
        return 0.0

    # Common ccxt shapes: balance['USDT'] -> {'free': x, 'used': y, 'total': z}
    if isinstance(bal, dict):
        # direct currency key
        if 'USDT' in bal and isinstance(bal['USDT'], dict):
            for k in ('free', 'available'):
                if k in bal['USDT']:
                    try:
                        return float(bal['USDT'][k])
                    except Exception:
                        pass
            # fallback to total
            if 'total' in bal['USDT']:
                try:
                    return float(bal['USDT']['total'])
                except Exception:
                    pass

        # some ccxt versions use top-level 'free'/'total' maps
        for map_key in ('free', 'total'):
            if map_key in bal and isinstance(bal[map_key], dict):
                v = bal[map_key].get('USDT')
                if v is not None:
                    try:
                        return float(v)
                    except Exception:
                        pass

        # try 'info' structure for Binance-specific shapes
        info = bal.get('info') if isinstance(bal, dict) else None
        if isinstance(info, dict):
            # Binance futures assets may be under info['assets'] (list)
            assets = info.get('assets') or info.get('positions')
            if isinstance(assets, list):
                for a in assets:
                    if a.get('asset') == 'USDT':
                        for key in ('availableBalance', 'walletBalance', 'balance'):
                            if key in a:
                                try:
                                    return float(a[key])
                                except Exception:
                                    pass

    return 0.0

def ejecutar_bot():
    lista_symbols = [s.strip() for s in os.getenv('SYMBOLS').split(',')]#['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'ADA/USDT']    
    print("🚀 BOT 1H SNIPER - ISOLATED MARGIN 🚀")

    # 1. Configuración inicial de margen (Solo se hace una vez)
    print("⚙️ Configurando cuentas...")
    exchange.load_markets()
    for symbol in lista_symbols:
        configurar_margen_aislado(symbol)
        configurar_apalancamiento_maximo(symbol)

    while True:
        print(f"\n⏰ Escaneo {time.strftime('%H:%M:%S')} (Timeframe 1H)")
        
        for symbol in lista_symbols:
            try:
                ticker = exchange.fetch_ticker(symbol)
                precio_actual = ticker['last']

                # 1. ¿Tenemos que salir?
                resultado = utilities.cerebro.monitorear_posicion(symbol, precio_actual)
                if resultado['accion'] == 'CERRAR':
                    try:
                        side = 'sell' if resultado['lado'] == 'long' else 'buy'
                        # Asegurarse de que la orden de cierre incluya el mismo `positionSide`
                        # que se usó al abrir la posición (necesario en HEDGE/dual-side mode).
                        position_side = 'LONG' if resultado['lado'] == 'long' else 'SHORT'
                        params = {'positionSide': position_side}
                        # Intentar usar un tamaño de cierre provisto por `resultado`, si existe.
                        amount = 1
                        if isinstance(resultado, dict):
                            amount = resultado.get('cantidad_monedas') or resultado.get('cantidad') or amount

                        exchange.create_market_order(symbol, side, amount, params=params)
                        print(f"✅ SALIDA EJECUTADA EN {symbol}")
                        # Salir del proceso tras ejecutar la salida
                        sys.exit(0)
                        #continue
                    except Exception as e:
                        print(f"❌ Salida fallo para {symbol}: {e}")

                # 2. ¿Hay nueva entrada?
                # Pasamos 'exchange' para que cerebro pueda bajar velas
                senal = utilities.cerebro.consultar_senal_mercado(exchange, symbol)
                
                if senal['entrar']:
                    lev = configurar_apalancamiento_maximo(symbol)
                    
                    # Cálculo de posición
                    # Margen = Cantidad USDT que quieres arriesgar
                    margen_usdt = senal['cantidad_usdt'] 
                    
                    # Tamaño orden = (Margen * Leverage) / Precio
                    cantidad_monedas = (margen_usdt * lev) / precio_actual
                    
                    side = 'buy' if senal['lado'] == 'long' else 'sell'
                    
                    print(f"⚡ ENTRANDO {side.upper()} {symbol} | Margen: ${margen_usdt} | Lev: {lev}x")
                    
                    try:
                        # Before creating the order, verify we have enough free USDT for the requested margin.
                        free_usdt = _get_free_usdt_balance()
                        required_margin = margen_usdt * 1.01  # small buffer for fees
                        if free_usdt < required_margin:
                            print(f"❌ Error orden: Margin insuficiente. Requerido: ${required_margin:.2f}, Disponible: ${free_usdt:.2f}")
                            # Try to reduce the margin to available funds if reasonable, otherwise skip
                            if free_usdt > 5:  # minimal threshold
                                new_margen = free_usdt * 0.95
                                cantidad_monedas = (new_margen * lev) / precio_actual
                                print(f"⚠️ Ajustando margen a ${new_margen:.2f} -> cantidad {cantidad_monedas:.6f}")
                            else:
                                print("❌ Salteando señal: saldo insuficiente para abrir posición.")
                                continue

                        # Binance futures may require a `positionSide` when account is in HEDGE mode.
                        position_side = 'LONG' if senal['lado'] == 'long' else 'SHORT'
                        params = {'positionSide': position_side}
                        exchange.create_market_order(symbol, side, cantidad_monedas, params=params)
                        utilities.cerebro.registrar_entrada(symbol, precio_actual, senal['lado'], lev)
                    except Exception as e:
                        err = str(e)
                        # Give a helpful hint for common Binance futures errors
                        if 'position side does not match' in err.lower() or '-4061' in err:
                            print(f"❌ Error orden: {e}\n   Hint: Your Binance futures account may be in ONE-WAY mode or the positionSide doesn't match.\n   - If you want dual-side positions, enable HEDGE mode in Binance UI.\n   - Or adjust your order params (`positionSide`: 'LONG'|'SHORT') to match your account settings.")
                        elif 'margin is insufficient' in err.lower() or '-2019' in err:
                            print(f"❌ Error orden: {e}\n   Hint: Your wallet doesn't have enough USDT to cover initial margin.\n   - Reduce `cantidad_usdt` in your signal or deposit more funds.\n   - You can also try lower leverage, e.g., call configurar_apalancamiento_maximo with lower value.")
                        else:
                            print(f"❌ Error orden: {e}")

            except Exception as e:
                print(f"⚠️ Error loop: {e}")
                time.sleep(1)
        
        # En timeframe de 1H, no necesitamos escanear cada segundo si usamos cierre de vela,
        # PERO como queremos salir "en cuanto retroceda", escaneamos rápido.
        time.sleep(5) 

if __name__ == "__main__":
    ejecutar_bot()