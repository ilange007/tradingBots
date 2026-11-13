import os
import sys
import pytest
import pandas as pd

# Ensure safe dummy environment variables before importing bot modules
os.environ['API_KEY'] = 'DUMMY_KEY'
os.environ['API_SECRET'] = 'DUMMY_SECRET'
os.environ['USE_TESTNET'] = 'false'
os.environ['SYMBOLS'] = 'BTC/USDT,ETH/USDT'
os.environ['CAPITAL'] = '20'

import utilities.cerebro as cerebro
import position_bot as position_bot


class MockExchange:
    """Mock ccxt Exchange adhering to interface used in position_bot and cerebro."""
    def __init__(self, config=None):
        self.config = config or {}
        self.options = self.config.get('options', {'defaultType': 'future'})
        self.orders = []
        self.leverage_calls = []
        self.margin_type_calls = []
        self.sandbox_calls = []
        self.markets = {
            'BTC/USDT': {'limits': {'leverage': {'max': 125}}},
            'ETH/USDT': {'limits': {'leverage': {'max': 100}}},
            'SOL/USDT': {'limits': {'leverage': {'max': 50}}},
        }
        self.tickers = {
            'BTC/USDT': {'last': 50000.0},
            'ETH/USDT': {'last': 3000.0},
            'SOL/USDT': {'last': 100.0},
        }
        self.balance = {
            'USDT': {'free': 1000.0, 'used': 0.0, 'total': 1000.0}
        }
        self.ohlcv_data = {}
        self.set_leverage_behavior = None  # None | 'type_error' | 'exception'

    def market(self, symbol):
        if symbol in self.markets:
            return self.markets[symbol]
        raise ValueError(f"Unknown symbol: {symbol}")

    def load_markets(self):
        return self.markets

    def set_leverage(self, *args, **kwargs):
        if self.set_leverage_behavior == 'type_error':
            self.set_leverage_behavior = None  # reset to avoid infinite loop
            raise TypeError("set_leverage argument ordering error")
        elif self.set_leverage_behavior == 'exception':
            raise Exception("Exchange leverage rejected")

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
            raise TypeError("Unexpected args count in set_leverage")

        self.leverage_calls.append((symbol, lev))
        return {'leverage': lev, 'symbol': symbol}

    def fapiPrivate_post_margintype(self, params):
        self.margin_type_calls.append(params)
        return {'code': 200, 'msg': 'success'}

    def set_sandbox_mode(self, flag):
        self.sandbox_calls.append(flag)

    def fetch_ticker(self, symbol):
        if symbol in self.tickers:
            return self.tickers[symbol]
        return {'last': 100.0}

    def fetch_balance(self):
        return self.balance

    def fetch_ohlcv(self, symbol, timeframe='1h', limit=300):
        if symbol in self.ohlcv_data:
            data = self.ohlcv_data[symbol]
            if isinstance(data, Exception):
                raise data
            return data
        # Default flat candles
        ts = 1600000000000
        bars = []
        for i in range(limit):
            bars.append([ts + i * 3600000, 100.0, 101.0, 99.0, 100.0, 50.0])
        return bars

    def create_market_order(self, symbol, side, amount, params=None):
        order = {
            'id': f'order-{len(self.orders) + 1}',
            'symbol': symbol,
            'side': side,
            'amount': amount,
            'params': params or {}
        }
        self.orders.append(order)
        return order


@pytest.fixture(autouse=True)
def clean_cerebro_state():
    """Ensure cerebro has a fresh state for every test."""
    cerebro.posiciones_activas.clear()
    cerebro.capital = 20.0
    yield
    cerebro.posiciones_activas.clear()


@pytest.fixture
def mock_exchange():
    """Provides a fresh MockExchange instance."""
    return MockExchange()


@pytest.fixture
def patch_position_bot_exchange(mock_exchange, monkeypatch):
    """Patches position_bot.exchange with mock_exchange."""
    monkeypatch.setattr(position_bot, 'exchange', mock_exchange)
    return mock_exchange


def generate_synthetic_ohlcv(pattern='bullish', limit=300):
    """
    Generate synthetic OHLCV data for technical indicator testing.
    Columns: [timestamp, open, high, low, close, volume]
    """
    ts = 1600000000000
    bars = []

    if pattern == 'bullish':
        # 250 flat candles at 100, then 50 candles rising to 200
        # Ensures price > SMA200 and MACDh > 0
        for i in range(250):
            bars.append([ts + i * 3600000, 100.0, 101.0, 99.0, 100.0, 100.0])
        for i in range(250, limit):
            p = 100.0 + (i - 249) * 2.0
            bars.append([ts + i * 3600000, p, p + 1.0, p - 1.0, p, 100.0])

    elif pattern == 'bearish':
        # 250 flat candles at 200, then 50 candles falling to 100
        # Ensures price < SMA200 and MACDh < 0
        for i in range(250):
            bars.append([ts + i * 3600000, 200.0, 201.0, 199.0, 200.0, 100.0])
        for i in range(250, limit):
            p = 200.0 - (i - 249) * 2.0
            bars.append([ts + i * 3600000, p, p + 1.0, p - 1.0, p, 100.0])

    elif pattern == 'lateral':
        # Flat constant prices, price == SMA200, MACDh == 0
        for i in range(limit):
            bars.append([ts + i * 3600000, 100.0, 100.5, 99.5, 100.0, 100.0])

    elif pattern == 'pullback':
        # Price > SMA200, but MACDh is negative (retracement)
        for i in range(250):
            bars.append([ts + i * 3600000, 100.0, 101.0, 99.0, 100.0, 100.0])
        for i in range(250, 280):
            p = 100.0 + (i - 249) * 1.5
            bars.append([ts + i * 3600000, p, p + 1.0, p - 1.0, p, 100.0])
        for i in range(280, limit):
            p = 145.0 - (i - 279) * 1.5
            bars.append([ts + i * 3600000, p, p + 1.0, p - 1.0, p, 100.0])

    elif pattern == 'nan':
        # Data containing NaN values in close
        for i in range(limit):
            close_val = float('nan') if i >= 280 else 100.0
            bars.append([ts + i * 3600000, 100.0, 101.0, 99.0, close_val, 100.0])

    elif pattern == 'insufficient':
        # Only 5 bars (less than 35 required for MACD)
        for i in range(5):
            bars.append([ts + i * 3600000, 100.0, 101.0, 99.0, 100.0, 100.0])

    elif pattern == 'empty':
        return []

    return bars


@pytest.fixture
def make_synthetic_bars():
    return generate_synthetic_ohlcv
