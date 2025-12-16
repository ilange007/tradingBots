"""Safe smoke test for position_bot.

This script mocks `ccxt.binance` and `utilities.cerebro` to run a single
setup + one simulated loop iteration without network calls.

Run from repository root (PowerShell):

    cd C:/Projects/tradingBots
    python ./smoke_test.py

Using forward slashes avoids Windows backslash escape warnings in docstrings.
"""
import sys, types, os

# Set safe env vars for the test
os.environ['API_KEY'] = 'DUMMY'
os.environ['API_SECRET'] = 'DUMMY'
os.environ['USE_TESTNET'] = 'false'

# --- Mock ccxt.binance ---
class MockExchange:
    def __init__(self, config=None):
        self.options = (config or {}).get('options', {})
        self._orders = []

    def market(self, symbol):
        return {'limits': {'leverage': {'max': 125}}}

    def set_leverage(self, *args, **kwargs):
        # Accept either (lev, symbol) or (symbol, lev)
        if len(args) == 2:
            a, b = args
            if isinstance(a, (int, float)):
                lev, symbol = a, b
            else:
                symbol, lev = a, b
        elif len(args) == 1:
            lev = args[0]
            symbol = kwargs.get('symbol')
        else:
            raise TypeError('Unexpected args')
        print(f"[MOCK] set_leverage called -> symbol={symbol}, lev={lev}")

    def fapiPrivate_post_margintype(self, params):
        print(f"[MOCK] fapiPrivate_post_margintype: {params}")

    def set_sandbox_mode(self, flag):
        print(f"[MOCK] set_sandbox_mode({flag})")

    def load_markets(self):
        print("[MOCK] load_markets() called")

    def fetch_ticker(self, symbol):
        return {'last': 50000.0}

    def fetch_ohlcv(self, symbol, timeframe='1h', limit=300):
        # Return simple synthetic OHLCV data: timestamps increasing, close rising
        bars = []
        ts = 1600000000000
        for i in range(limit):
            open_p = 40000 + i
            close = open_p + 10
            bars.append([ts + i*3600000, open_p, open_p+20, open_p-10, close, 100])
        return bars

    def create_market_order(self, symbol, side, amount, params=None):
        self._orders.append((symbol, side, amount, params))
        print(f"[MOCK] create_market_order -> {symbol} {side} {amount} {params}")
        return {'id': 'mock-order-1'}

# Create a fake ccxt module
mock_ccxt = types.ModuleType('ccxt')
mock_ccxt.binance = lambda cfg=None: MockExchange(cfg)
sys.modules['ccxt'] = mock_ccxt

# --- Mock utilities.cerebro to avoid pandas/pandas_ta dependencies ---
mock_cerebro = types.ModuleType('utilities.cerebro')

def mock_consultar_senal_mercado(exchange, symbol):
    # For smoke test, return a signal to enter once
    return {'entrar': True, 'lado': 'long', 'cantidad_usdt': 10}

def mock_registrar_entrada(symbol, precio_entrada, lado):
    print(f"[MOCK CEREBRO] registrar_entrada: {symbol} {precio_entrada} {lado}")

def mock_monitorear_posicion(symbol, precio_actual):
    return 'NADA'

mock_cerebro.consultar_senal_mercado = mock_consultar_senal_mercado
mock_cerebro.registrar_entrada = mock_registrar_entrada
mock_cerebro.monitorear_posicion = mock_monitorear_posicion
sys.modules['utilities.cerebro'] = mock_cerebro

# Import and run smoke actions
import importlib
if 'position_bot' in sys.modules:
    del sys.modules['position_bot']
import position_bot as pb

print('\n--- Running smoke actions ---')
# Call the initial setup functions
pb.exchange.load_markets()
for s in ['BTC/USDT','ETH/USDT']:
    pb.configurar_margen_aislado(s)
    lev = pb.configurar_apalancamiento_maximo(s)
    print(f"Configured {s} leverage -> {lev}")

# Simulate one iteration of main loop logic for a symbol
symbol = 'BTC/USDT'
ticker = pb.exchange.fetch_ticker(symbol)
precio_actual = ticker['last']
accion = mock_cerebro.monitorear_posicion(symbol, precio_actual)
print('accion monitorear_posicion ->', accion)
senal = mock_cerebro.consultar_senal_mercado(pb.exchange, symbol)
print('senal ->', senal)
if senal['entrar']:
    lev = pb.configurar_apalancamiento_maximo(symbol)
    margen_usdt = senal['cantidad_usdt']
    cantidad_monedas = (margen_usdt * lev) / precio_actual
    side = 'buy' if senal['lado'] == 'long' else 'sell'
    pb.exchange.create_market_order(symbol, side, cantidad_monedas)
    mock_cerebro.registrar_entrada(symbol, precio_actual, senal['lado'])

print('\nSMOKE TEST COMPLETED')
