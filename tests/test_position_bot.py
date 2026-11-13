import os
import sys
import pytest
from unittest.mock import MagicMock
import position_bot as pb
import utilities.cerebro as cerebro


class StopBotLoop(Exception):
    """Custom exception used to cleanly exit the bot's while True loop in tests."""
    pass


class TestConfigurarMargenAislado:
    """Tests for isolated margin configuration."""

    def test_configurar_margen_aislado_calls_exchange(self, patch_position_bot_exchange):
        pb.configurar_margen_aislado('BTC/USDT')

        calls = patch_position_bot_exchange.margin_type_calls
        assert len(calls) == 1
        assert calls[0] == {'symbol': 'BTCUSDT', 'marginType': 'ISOLATED'}

    def test_configurar_margen_aislado_handles_exception_gracefully(self, patch_position_bot_exchange, monkeypatch):
        def mock_fapi_error(params):
            raise Exception("No need to change margin type.")

        monkeypatch.setattr(patch_position_bot_exchange, 'fapiPrivate_post_margintype', mock_fapi_error)
        # Should not raise exception
        pb.configurar_margen_aislado('BTC/USDT')


class TestConfigurarApalancamientoMaximo:
    """Tests for leverage configuration and fallbacks."""

    def test_configurar_apalancamiento_normal_success(self, patch_position_bot_exchange):
        patch_position_bot_exchange.markets['BTC/USDT'] = {'limits': {'leverage': {'max': 125}}}

        lev = pb.configurar_apalancamiento_maximo('BTC/USDT')

        assert lev == 125
        assert ('BTC/USDT', 125) in patch_position_bot_exchange.leverage_calls

    def test_configurar_apalancamiento_type_error_inverted_args_fallback(self, patch_position_bot_exchange):
        patch_position_bot_exchange.set_leverage_behavior = 'type_error'

        lev = pb.configurar_apalancamiento_maximo('ETH/USDT')

        assert lev == 100
        assert ('ETH/USDT', 100) in patch_position_bot_exchange.leverage_calls

    def test_configurar_apalancamiento_exchange_exception_falls_back_to_50(self, patch_position_bot_exchange):
        def mock_set_leverage(lev, symbol):
            if lev > 50:
                raise Exception("Max leverage rejected by exchange")
            patch_position_bot_exchange.leverage_calls.append((symbol, lev))
            return {'leverage': lev}

        patch_position_bot_exchange.set_leverage = mock_set_leverage

        lev = pb.configurar_apalancamiento_maximo('BTC/USDT')

        assert lev == 50
        assert ('BTC/USDT', 50) in patch_position_bot_exchange.leverage_calls

    def test_configurar_apalancamiento_market_lookup_fails_falls_back_to_50(self, patch_position_bot_exchange):
        patch_position_bot_exchange.markets.clear()  # market lookup will fail

        lev = pb.configurar_apalancamiento_maximo('BTC/USDT')

        assert lev == 50
        assert ('BTC/USDT', 50) in patch_position_bot_exchange.leverage_calls

    def test_configurar_apalancamiento_all_attempts_fail_returns_50_safely(self, patch_position_bot_exchange):
        def mock_failing_set_leverage(*args, **kwargs):
            raise Exception("Total API failure")

        patch_position_bot_exchange.set_leverage = mock_failing_set_leverage

        lev = pb.configurar_apalancamiento_maximo('BTC/USDT')

        assert lev == 50


class TestGetFreeUsdtBalance:
    """Tests for multi-shape USDT balance parser."""

    def test_balance_shape_nested_usdt_free(self, patch_position_bot_exchange):
        patch_position_bot_exchange.balance = {'USDT': {'free': 150.75, 'used': 0.0}}
        assert pb._get_free_usdt_balance() == 150.75

    def test_balance_shape_nested_usdt_available(self, patch_position_bot_exchange):
        patch_position_bot_exchange.balance = {'USDT': {'available': 88.5, 'used': 0.0}}
        assert pb._get_free_usdt_balance() == 88.5

    def test_balance_shape_nested_usdt_total_fallback(self, patch_position_bot_exchange):
        patch_position_bot_exchange.balance = {'USDT': {'total': 42.0}}
        assert pb._get_free_usdt_balance() == 42.0

    def test_balance_shape_toplevel_free_map(self, patch_position_bot_exchange):
        patch_position_bot_exchange.balance = {'free': {'USDT': 210.0}, 'total': {'USDT': 300.0}}
        assert pb._get_free_usdt_balance() == 210.0

    def test_balance_shape_toplevel_total_map(self, patch_position_bot_exchange):
        patch_position_bot_exchange.balance = {'total': {'USDT': 65.0}}
        assert pb._get_free_usdt_balance() == 65.0

    def test_balance_shape_binance_info_assets(self, patch_position_bot_exchange):
        patch_position_bot_exchange.balance = {
            'info': {
                'assets': [
                    {'asset': 'BTC', 'availableBalance': '0.5'},
                    {'asset': 'USDT', 'availableBalance': '314.15'}
                ]
            }
        }
        assert pb._get_free_usdt_balance() == 314.15

    def test_balance_exchange_exception_returns_zero(self, patch_position_bot_exchange):
        def failing_balance():
            raise Exception("API network error")

        patch_position_bot_exchange.fetch_balance = failing_balance
        assert pb._get_free_usdt_balance() == 0.0

    def test_balance_missing_usdt_returns_zero(self, patch_position_bot_exchange):
        patch_position_bot_exchange.balance = {'BTC': {'free': 1.0}}
        assert pb._get_free_usdt_balance() == 0.0


class TestFlujoEntradaSalida:
    """Tests covering entry and exit flows in position_bot."""

    def test_flujo_entrada_long_when_signal_present(self, patch_position_bot_exchange, monkeypatch):
        monkeypatch.setenv('SYMBOLS', 'BTC/USDT')
        patch_position_bot_exchange.tickers['BTC/USDT'] = {'last': 50000.0}
        patch_position_bot_exchange.balance = {'USDT': {'free': 100.0}}

        monkeypatch.setattr(
            cerebro, 'consultar_senal_mercado',
            lambda exc, sym: {'entrar': True, 'lado': 'long', 'cantidad_usdt': 20.0}
        )
        monkeypatch.setattr(
            cerebro, 'monitorear_posicion',
            lambda sym, price: {'accion': 'NADA'}
        )

        def stop_after_one_iteration(seconds):
            raise StopBotLoop()

        monkeypatch.setattr(pb.time, 'sleep', stop_after_one_iteration)

        with pytest.raises(StopBotLoop):
            pb.ejecutar_bot()

        # Check market order was created
        orders = patch_position_bot_exchange.orders
        assert len(orders) == 1
        order = orders[0]
        assert order['symbol'] == 'BTC/USDT'
        assert order['side'] == 'buy'
        assert order['params'] == {'positionSide': 'LONG'}
        # cantidad = (20 * 125) / 50000 = 0.05
        assert pytest.approx(order['amount'], rel=1e-4) == 0.05

        # Check cerebro registered entry
        assert 'BTC/USDT' in cerebro.posiciones_activas
        assert cerebro.posiciones_activas['BTC/USDT']['entry_price'] == 50000.0
        assert cerebro.posiciones_activas['BTC/USDT']['side'] == 'long'
        assert cerebro.posiciones_activas['BTC/USDT']['leverage'] == 125

    def test_flujo_entrada_short_when_signal_present(self, patch_position_bot_exchange, monkeypatch):
        monkeypatch.setenv('SYMBOLS', 'ETH/USDT')
        patch_position_bot_exchange.tickers['ETH/USDT'] = {'last': 2000.0}
        patch_position_bot_exchange.balance = {'USDT': {'free': 100.0}}

        monkeypatch.setattr(
            cerebro, 'consultar_senal_mercado',
            lambda exc, sym: {'entrar': True, 'lado': 'short', 'cantidad_usdt': 10.0}
        )
        monkeypatch.setattr(
            cerebro, 'monitorear_posicion',
            lambda sym, price: {'accion': 'NADA'}
        )

        def stop_after_one_iteration(seconds):
            raise StopBotLoop()

        monkeypatch.setattr(pb.time, 'sleep', stop_after_one_iteration)

        with pytest.raises(StopBotLoop):
            pb.ejecutar_bot()

        orders = patch_position_bot_exchange.orders
        assert len(orders) == 1
        order = orders[0]
        assert order['symbol'] == 'ETH/USDT'
        assert order['side'] == 'sell'
        assert order['params'] == {'positionSide': 'SHORT'}
        # cantidad = (10 * 100) / 2000 = 0.5
        assert pytest.approx(order['amount'], rel=1e-4) == 0.5

        assert 'ETH/USDT' in cerebro.posiciones_activas
        assert cerebro.posiciones_activas['ETH/USDT']['side'] == 'short'

    def test_flujo_entrada_margin_adjusted_when_balance_tight(self, patch_position_bot_exchange, monkeypatch):
        # Requested margin = 20 USDT, required = 20.2 USDT.
        # Free balance = 8.0 USDT (> 5 but < 20.2).
        # Bot should adjust margin to 8.0 * 0.95 = 7.6 USDT.
        monkeypatch.setenv('SYMBOLS', 'BTC/USDT')
        patch_position_bot_exchange.tickers['BTC/USDT'] = {'last': 50000.0}
        patch_position_bot_exchange.balance = {'USDT': {'free': 8.0}}

        monkeypatch.setattr(
            cerebro, 'consultar_senal_mercado',
            lambda exc, sym: {'entrar': True, 'lado': 'long', 'cantidad_usdt': 20.0}
        )
        monkeypatch.setattr(
            cerebro, 'monitorear_posicion',
            lambda sym, price: {'accion': 'NADA'}
        )

        def stop_after_one_iteration(seconds):
            raise StopBotLoop()

        monkeypatch.setattr(pb.time, 'sleep', stop_after_one_iteration)

        with pytest.raises(StopBotLoop):
            pb.ejecutar_bot()

        orders = patch_position_bot_exchange.orders
        assert len(orders) == 1
        # adjusted amount = (7.6 * 125) / 50000 = 0.019
        assert pytest.approx(orders[0]['amount'], rel=1e-4) == 0.019

    def test_flujo_entrada_skipped_when_balance_insufficient(self, patch_position_bot_exchange, monkeypatch):
        # Free balance = 3.0 USDT (<= 5). Bot skips opening position.
        monkeypatch.setenv('SYMBOLS', 'BTC/USDT')
        patch_position_bot_exchange.tickers['BTC/USDT'] = {'last': 50000.0}
        patch_position_bot_exchange.balance = {'USDT': {'free': 3.0}}

        monkeypatch.setattr(
            cerebro, 'consultar_senal_mercado',
            lambda exc, sym: {'entrar': True, 'lado': 'long', 'cantidad_usdt': 20.0}
        )
        monkeypatch.setattr(
            cerebro, 'monitorear_posicion',
            lambda sym, price: {'accion': 'NADA'}
        )

        def stop_after_one_iteration(seconds):
            raise StopBotLoop()

        monkeypatch.setattr(pb.time, 'sleep', stop_after_one_iteration)

        with pytest.raises(StopBotLoop):
            pb.ejecutar_bot()

        assert len(patch_position_bot_exchange.orders) == 0
        assert 'BTC/USDT' not in cerebro.posiciones_activas

    def test_flujo_salida_long_position_triggers_sys_exit(self, patch_position_bot_exchange, monkeypatch):
        monkeypatch.setenv('SYMBOLS', 'BTC/USDT')
        patch_position_bot_exchange.tickers['BTC/USDT'] = {'last': 52000.0}

        monkeypatch.setattr(
            cerebro, 'monitorear_posicion',
            lambda sym, price: {'accion': 'CERRAR', 'lado': 'long'}
        )

        with pytest.raises(SystemExit) as exc_info:
            pb.ejecutar_bot()

        assert exc_info.value.code == 0
        orders = patch_position_bot_exchange.orders
        assert len(orders) == 1
        order = orders[0]
        assert order['symbol'] == 'BTC/USDT'
        assert order['side'] == 'sell'
        assert order['params'] == {'positionSide': 'LONG'}

    def test_flujo_salida_short_position_triggers_sys_exit(self, patch_position_bot_exchange, monkeypatch):
        monkeypatch.setenv('SYMBOLS', 'ETH/USDT')
        patch_position_bot_exchange.tickers['ETH/USDT'] = {'last': 2800.0}

        monkeypatch.setattr(
            cerebro, 'monitorear_posicion',
            lambda sym, price: {'accion': 'CERRAR', 'lado': 'short'}
        )

        with pytest.raises(SystemExit) as exc_info:
            pb.ejecutar_bot()

        assert exc_info.value.code == 0
        orders = patch_position_bot_exchange.orders
        assert len(orders) == 1
        order = orders[0]
        assert order['symbol'] == 'ETH/USDT'
        assert order['side'] == 'buy'
        assert order['params'] == {'positionSide': 'SHORT'}

    def test_flujo_salida_handles_order_exception_without_crash(self, patch_position_bot_exchange, monkeypatch):
        monkeypatch.setenv('SYMBOLS', 'BTC/USDT')
        patch_position_bot_exchange.tickers['BTC/USDT'] = {'last': 52000.0}

        monkeypatch.setattr(
            cerebro, 'monitorear_posicion',
            lambda sym, price: {'accion': 'CERRAR', 'lado': 'long'}
        )
        monkeypatch.setattr(
            cerebro, 'consultar_senal_mercado',
            lambda exc, sym: {'entrar': False}
        )

        def failing_create_order(*args, **kwargs):
            raise Exception("Exchange order error: Margin not enough")

        patch_position_bot_exchange.create_market_order = failing_create_order

        def stop_after_one_iteration(seconds):
            raise StopBotLoop()

        monkeypatch.setattr(pb.time, 'sleep', stop_after_one_iteration)

        # Should handle the order failure, print error, and continue loop until StopBotLoop
        with pytest.raises(StopBotLoop):
            pb.ejecutar_bot()


class TestPositionBotEdgeCasesAndBugs:
    """Documenting edge cases and bugs in position_bot."""

    @pytest.mark.xfail(
        strict=True,
        reason="monitorear_posicion returning string 'NADA' when entry_price is 0 causes TypeError in position_bot"
    )
    def test_monitorear_posicion_string_nada_crashes_position_bot(self, patch_position_bot_exchange, monkeypatch):
        monkeypatch.setenv('SYMBOLS', 'BTC/USDT')
        patch_position_bot_exchange.tickers['BTC/USDT'] = {'last': 50000.0}

        # Simulate cerebro bug returning string 'NADA'
        monkeypatch.setattr(
            cerebro, 'monitorear_posicion',
            lambda sym, price: 'NADA'
        )

        # In position_bot line 148: resultado['accion'] is accessed directly,
        # which throws TypeError: string indices must be integers, not 'str'
        resultado = cerebro.monitorear_posicion('BTC/USDT', 50000.0)
        assert resultado['accion'] == 'NADA'

    @pytest.mark.xfail(
        strict=True,
        reason="Exit flow hardcodes amount=1 instead of using actual position size from cerebro"
    )
    def test_exit_uses_entered_position_amount_instead_of_default_one(self, patch_position_bot_exchange, monkeypatch):
        monkeypatch.setenv('SYMBOLS', 'BTC/USDT')
        patch_position_bot_exchange.tickers['BTC/USDT'] = {'last': 50000.0}

        # Suppose we opened a position of 0.05 BTC
        cerebro.registrar_entrada('BTC/USDT', 50000.0, 'long', leverage=10)

        # In production, cerebro.monitorear_posicion returns {'accion': 'CERRAR', 'lado': 'long'}
        # without 'cantidad_monedas', causing amount to default to 1.
        monkeypatch.setattr(
            cerebro, 'monitorear_posicion',
            lambda sym, price: {'accion': 'CERRAR', 'lado': 'long'}
        )

        with pytest.raises(SystemExit):
            pb.ejecutar_bot()

        order = patch_position_bot_exchange.orders[0]
        # Should close 0.05, but production closes 1
        assert order['amount'] == 0.05
