import sys
import ccxt
import os
import time
import math
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

def safe_create_market_order(exchange, symbol, side, amount, params=None):
    """Intento seguro para crear market orders en Binance.
    - Si Binance responde con error de "position side does not match" (-4061),
      reintenta sin el parámetro `positionSide`.
    - Normaliza `positionSide` si viene en minúsculas/incorrecto.
    """
    if params is None:
        params = {}

    last_exc = None
    # Attempt 1: normal create_market_order
    try:
        return exchange.create_market_order(symbol, side, amount, params=params)
    except Exception as e:
        last_exc = e
        err = str(e)
        print(f"⚠️ create_market_order fallo: {err}")

    # If position side mismatch, try removing positionSide
    try:
        err_lower = str(last_exc).lower() if last_exc is not None else ''
        if 'position side does not match' in err_lower or '-4061' in err_lower:
            print(f"⚠️ position side mismatch detected, reintentando sin 'positionSide' para {symbol}")
            params_copy = {k: v for k, v in (params or {}).items() if k.lower() != 'positionside'}
            try:
                return exchange.create_market_order(symbol, side, amount, params=params_copy)
            except Exception as e2:
                print(f"⚠️ Reintento create_market_order sin positionSide falló: {e2}")
                last_exc = e2

    except Exception:
        pass

    # Attempt 3: use create_order (explicit type) which sometimes maps differently in ccxt
    try:
        print(f"ℹ️ Intentando create_order('market') para {symbol}")
        return exchange.create_order(symbol, 'market', side, amount, params=params)
    except Exception as e:
        print(f"⚠️ create_order('market') fallo: {e}")
        last_exc = e

    # If Binance complains about reduceOnly (-1106), retry without that flag
    try:
        err_lower = str(last_exc).lower() if last_exc is not None else ''
        if 'reduceonly' in err_lower or '-1106' in err_lower:
            print(f"⚠️ Binance indicó que 'reduceOnly' no es requerido; reintentando sin 'reduceOnly' para {symbol}")
            params_copy = {k: v for k, v in (params or {}).items() if k.lower() != 'reduceonly'}
            try:
                return exchange.create_market_order(symbol, side, amount, params=params_copy)
            except Exception as e2:
                print(f"⚠️ Reintento sin reduceOnly falló: {e2}")
                last_exc = e2
    except Exception:
        pass

    # If still failing, raise the last exception
    if last_exc:
        raise last_exc
    else:
        raise Exception('Unknown error creating market order')

def get_open_position_amount(exchange, symbol, pos=None):
    """Devuelve la cantidad abierta (en unidades del activo base) para `symbol`.
    Intenta varios campos comunes retornados por ccxt/Binance: info.positionAmt, contracts, size, amount, positionAmt.
    Si no puede encontrar una cantidad válida, devuelve 0.0.
    """
    try:
        if pos is None:
            positions = exchange.fetch_positions([symbol])
            if not positions or len(positions) == 0:
                return 0.0
            pos = positions[0]

        info = pos.get('info') if isinstance(pos, dict) else {}

        # Campos comunes dentro de info
        candidates = []
        if isinstance(info, dict):
            candidates.extend([info.get(k) for k in ('positionAmt', 'positionAmtStr', 'positionAmt', 'amount', 'size')])

        # Campos a nivel top-level en pos
        candidates.extend([pos.get(k) for k in ('contracts', 'size', 'amount', 'positionAmt')])

        for v in candidates:
            if v is None:
                continue
            try:
                return abs(float(v))
            except Exception:
                try:
                    # A veces viene con comas o como string raro
                    return abs(float(str(v).replace(',', '')))
                except Exception:
                    continue
    except Exception as e:
        print(f"⚠️ get_open_position_amount error: {e}")

    return 0.0

def get_signed_position_amount(exchange, symbol, pos=None):
    """Devuelve la cantidad abierta con signo (+ para long, - para short).
    Intenta varios campos comunes que pueden contener signo: info.positionAmt, positionAmt, amount.
    Retorna 0.0 si no se encuentra información válida.
    """
    try:
        if pos is None:
            positions = exchange.fetch_positions([symbol])
            if not positions or len(positions) == 0:
                return 0.0
            pos = positions[0]

        info = pos.get('info') if isinstance(pos, dict) else {}

        candidates = []
        if isinstance(info, dict):
            candidates.extend([info.get(k) for k in ('positionAmt', 'positionAmtStr', 'amount', 'size')])

        candidates.extend([pos.get(k) for k in ('positionAmt', 'amount', 'contracts', 'size')])

        for v in candidates:
            if v is None:
                continue
            try:
                return float(v)
            except Exception:
                try:
                    return float(str(v).replace(',', ''))
                except Exception:
                    continue
    except Exception as e:
        print(f"⚠️ get_signed_position_amount error: {e}")

    return 0.0

def adjust_amount_to_market(exchange, symbol, amount):
    """Ajusta `amount` a la precisión y mínimos del mercado para `symbol`.
    Devuelve 0.0 si la cantidad ajustada queda por debajo del mínimo permitido.
    """
    try:
        market = exchange.market(symbol)
        precision = None
        limits = {}
        if isinstance(market, dict):
            precision = market.get('precision', {}).get('amount')
            limits = market.get('limits', {}).get('amount', {}) or {}
        min_amt = limits.get('min')

        # If there's no explicit min amount, try to infer it from min cost (quote currency)
        if min_amt is None:
            try:
                cost_limits = market.get('limits', {}).get('cost', {}) or {}
                min_cost = cost_limits.get('min')
                if min_cost is not None:
                    # fetch current price to convert min_cost (quote) -> min_amount (base)
                    try:
                        ticker = exchange.fetch_ticker(symbol)
                        price = float(ticker.get('last') or ticker.get('close') or 0)
                        if price and price > 0:
                            min_amt = float(min_cost) / price
                        else:
                            min_amt = None
                    except Exception:
                        min_amt = None
            except Exception:
                min_amt = None

        if precision is not None:
            factor = 10 ** precision
            amount = math.floor(amount * factor) / factor

        if min_amt is not None and amount < min_amt:
            print(f"⚠️ adjust_amount_to_market: cantidad {amount} < min_amt {min_amt} para {symbol}")
            return 0.0

        return amount
    except Exception as e:
        print(f"⚠️ adjust_amount_to_market error: {e}")
        return amount

def adjust_order_for_available_margin(exchange, symbol, cantidad_monedas, precio_actual, lev):
    """Intenta ajustar `lev` o `cantidad_monedas` para que el margen requerido quepa en el balance libre.
    Retorna (cantidad_monedas, lev, mensaje) donde mensaje es None si OK o explicación si no es posible.
    """
    try:
        free_usdt = _get_free_usdt_balance()

        # calcular notional
        notional = cantidad_monedas * precio_actual
        # required initial margin estimate
        required_margin = notional / lev if lev and lev > 0 else float('inf')
        if free_usdt >= required_margin * 1.01:
            return cantidad_monedas, lev, None

        # Intentar aumentar leverage hasta el máximo permitido por el mercado (reduce required margin)
        market = exchange.market(symbol)
        market_max_lev = None
        if isinstance(market, dict):
            try:
                market_max_lev = int(market.get('limits', {}).get('leverage', {}).get('max') or 0)
            except Exception:
                market_max_lev = None

        if market_max_lev and market_max_lev > lev:
            req_with_max = notional / market_max_lev
            if free_usdt >= req_with_max * 1.01:
                return cantidad_monedas, market_max_lev, f"increased_leverage_to_{market_max_lev}"

        # si no cabe aumentando leverage, reducir tamaño hasta lo máximo asequible usando market_max_lev (o lev si no hay max)
        use_lev = market_max_lev or lev
        max_qty = (free_usdt * 0.95 * use_lev) / precio_actual if precio_actual > 0 else 0
        # ajustar a precision del mercado
        market = exchange.market(symbol)
        precision = market.get('precision', {}).get('amount') if isinstance(market, dict) else None
        if precision is not None and max_qty > 0:
            factor = 10 ** precision
            max_qty = math.floor(max_qty * factor) / factor

        if max_qty <= 0:
            return cantidad_monedas, lev, f"insufficient_margin_free:{free_usdt:.2f}"

        # verificar notional mínimo
        cost_min = None
        if isinstance(market, dict):
            cost_min = market.get('limits', {}).get('cost', {}).get('min')
        if cost_min is not None and precio_actual > 0:
            min_qty_for_cost = float(cost_min) / precio_actual
            if max_qty < min_qty_for_cost:
                return cantidad_monedas, lev, f"max_qty {max_qty} < min_qty_for_cost {min_qty_for_cost}"

        return max_qty, use_lev, "reduced_size_to_max_affordable"
    except Exception as e:
        return cantidad_monedas, lev, f"error_adjust_margin:{e}"


def open_position_simple(exchange, symbol, lado, margen_usdt):
    """Abrir posición de forma simple: calcula qty desde margen_usdt y lev,
    aplica precision mínima y notional, verifica balance simple y ejecuta market order.
    Retorna True si se envió la orden, False en caso contrario.
    """
    try:
        precio = float(exchange.fetch_ticker(symbol)['last'])
    except Exception as e:
        print(f"⚠️ open_position_simple: no se pudo obtener precio para {symbol}: {e}")
        return False

    lev = configurar_apalancamiento_maximo(symbol)
    cantidad = (margen_usdt * lev) / precio if precio and lev else 0

    # Aplicar precision del mercado (floor)
    try:
        market = exchange.market(symbol)
        precision = market.get('precision', {}).get('amount') if isinstance(market, dict) else None
        if precision is not None:
            factor = 10 ** precision
            cantidad = math.floor(cantidad * factor) / factor
        # comprobar notional mínimo
        cost_min = market.get('limits', {}).get('cost', {}).get('min') if isinstance(market, dict) else None
        if cost_min is not None:
            notional = cantidad * precio
            if notional < float(cost_min):
                # ajustar a cantidad mínima requerida
                needed = float(cost_min) / precio
                if precision is not None:
                    needed = math.ceil(needed * (10 ** precision)) / (10 ** precision)
                cantidad = needed
    except Exception:
        pass

    if cantidad <= 0:
        print(f"❌ open_position_simple: cantidad inválida para {symbol} -> {cantidad}")
        return False

    # Verificar balance simple y autoreducir margen_usdt si es necesario
    free = _get_free_usdt_balance()
    if free < margen_usdt * 1.01:
        # Intento automático: reducir margen iterativamente (mitades) hasta ajustarse o hasta un umbral mínimo
        min_margen = 1.0
        reduced = float(margen_usdt)
        attempted = False
        while reduced > min_margen and free < reduced * 1.01:
            reduced = max(reduced * 0.5, min_margen)
            attempted = True
        if attempted and free >= reduced * 1.01:
            print(f"⚠️ open_position_simple: margen insuficiente, autoreduciendo {margen_usdt:.2f} -> {reduced:.2f} para {symbol}")
            margen_usdt = reduced
            # recalcular cantidad con margen reducido
            cantidad = (margen_usdt * lev) / precio if precio and lev else 0
            try:
                if precision is not None:
                    factor = 10 ** precision
                    cantidad = math.floor(cantidad * factor) / factor
            except Exception:
                pass
        else:
            # Intentar colocar posición mínima según mercado (cost min -> min_qty)
            try:
                market = exchange.market(symbol)
                cost_min = market.get('limits', {}).get('cost', {}).get('min') if isinstance(market, dict) else None
                if cost_min is not None and precio > 0:
                    min_qty = float(cost_min) / precio
                    # aplicar precision
                    try:
                        if precision is not None:
                            factor = 10 ** precision
                            min_qty = math.ceil(min_qty * factor) / factor
                    except Exception:
                        pass
                    required_margin_for_min = (float(cost_min)) / lev if lev else float('inf')
                    if free >= required_margin_for_min * 1.01:
                        print(f"⚠️ open_position_simple: usando min_qty {min_qty} para {symbol} porque margen reducido no encaja")
                        cantidad = min_qty
                        margen_usdt = (cantidad * precio) / lev if lev else 0
                    else:
                        print(f"❌ open_position_simple: margen insuficiente incluso para min notional {cost_min} (Libre: {free:.2f})")
                        return False
                else:
                    print(f"❌ open_position_simple: margen insuficiente para {symbol}. Necesario: {margen_usdt:.2f}, Libre: {free:.2f}")
                    return False
            except Exception:
                print(f"❌ open_position_simple: margen insuficiente y no se pudo calcular min_qty para {symbol}")
                return False

    params = {}
    try:
        params['positionSide'] = 'LONG' if lado == 'long' else 'SHORT'
    except Exception:
        pass

    try:
        safe_create_market_order(exchange, symbol, 'buy' if lado == 'long' else 'sell', cantidad, params=params)
        utilities.cerebro.registrar_entrada(symbol, precio, lado, lev)
        print(f"✅ ENTRADA (simple) enviada {symbol} qty={cantidad} lev={lev}x")
        return True
    except Exception as e:
        print(f"❌ open_position_simple fallo creando orden para {symbol}: {e}")
        return False


def close_position_simple(exchange, symbol):
    """Cerrar posición abierta de forma simple: obtiene posición, deriva lado y qty, y envía market reduceOnly."""
    try:
        positions = exchange.fetch_positions([symbol])
    except Exception as e:
        print(f"⚠️ close_position_simple: no se pudo fetch_positions para {symbol}: {e}")
        return False

    if not positions or len(positions) == 0:
        print(f"⚠️ close_position_simple: no hay posiciones para {symbol}")
        return False

    pos = positions[0]
    signed = get_signed_position_amount(exchange, symbol, pos)
    if signed == 0:
        raw = get_open_position_amount(exchange, symbol, pos)
        if raw <= 0:
            print(f"⚠️ close_position_simple: no se pudo determinar cantidad a cerrar para {symbol}")
            return False
        qty = raw
        lado_pos = 'long' if raw > 0 else 'short'
    else:
        qty = abs(signed)
        lado_pos = 'long' if signed > 0 else 'short'

    # Ajustar precision
    try:
        market = exchange.market(symbol)
        precision = market.get('precision', {}).get('amount') if isinstance(market, dict) else None
        if precision is not None:
            factor = 10 ** precision
            qty = math.floor(qty * factor) / factor
    except Exception:
        pass

    if qty <= 0:
        print(f"❌ close_position_simple: cantidad final inválida para {symbol} -> {qty}")
        return False

    side = 'sell' if lado_pos == 'long' else 'buy'
    params = {'reduceOnly': True}
    try:
        info = pos.get('info') if isinstance(pos, dict) else {}
        pside = info.get('positionSide') if isinstance(info, dict) else None
        if not pside:
            pside = pos.get('positionSide')
        if isinstance(pside, str) and pside:
            params['positionSide'] = pside.upper() if pside.islower() else pside
    except Exception:
        pass

    try:
        safe_create_market_order(exchange, symbol, side, qty, params=params)
        print(f"✅ SALIDA (simple) enviada {symbol} qty={qty} side={side} positionSide={params.get('positionSide')}")
        return True
    except Exception as e:
        print(f"❌ close_position_simple fallo para {symbol}: {e}")
        return False

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
                    ok = close_position_simple(exchange, symbol)
                    if not ok:
                        print(f"⚠️ No se ejecutó cierre simple para {symbol}")
                    continue

                # 2. ¿Hay nueva entrada?
                # Pasamos 'exchange' para que cerebro pueda bajar velas
                senal = utilities.cerebro.consultar_senal_mercado(exchange, symbol)
                
                if senal['entrar']:
                    ok = open_position_simple(exchange, symbol, senal['lado'], senal.get('cantidad_usdt', 0))
                    if not ok:
                        print(f"⚠️ No se ejecutó entrada simple para {symbol}")

            except Exception as e:
                print(f"⚠️ Error loop: {e}")
                time.sleep(1)
        
        # En timeframe de 1H, no necesitamos escanear cada segundo si usamos cierre de vela,
        # PERO como queremos salir "en cuanto retroceda", escaneamos rápido.
        time.sleep(5) 

if __name__ == "__main__":
    ejecutar_bot()