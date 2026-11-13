import pytest
import pandas as pd
import utilities.cerebro as cerebro


class TestObtenerDataHistorica:
    """Tests for obtaining OHLCV data from exchange."""

    def test_obtener_data_historica_success(self, mock_exchange, make_synthetic_bars):
        mock_exchange.ohlcv_data['BTC/USDT'] = make_synthetic_bars('bullish', limit=300)
        df = cerebro.obtener_data_historica(mock_exchange, 'BTC/USDT')

        assert isinstance(df, pd.DataFrame)
        assert len(df) == 300
        assert list(df.columns) == ['timestamp', 'open', 'high', 'low', 'close', 'volume']

    def test_obtener_data_historica_exception_returns_none(self, mock_exchange):
        mock_exchange.ohlcv_data['BTC/USDT'] = Exception("Network timeout")
        df = cerebro.obtener_data_historica(mock_exchange, 'BTC/USDT')

        assert df is None


class TestConsultarSenalMercado:
    """Tests for market signal generation (SMA200 + MACD + RSI)."""

    def test_signal_ignored_if_position_already_active(self, mock_exchange, make_synthetic_bars):
        mock_exchange.ohlcv_data['BTC/USDT'] = make_synthetic_bars('bullish')
        cerebro.registrar_entrada('BTC/USDT', 50000.0, 'long', leverage=10)

        signal = cerebro.consultar_senal_mercado(mock_exchange, 'BTC/USDT')
        assert signal == {'entrar': False}

    def test_signal_returns_false_when_data_is_none(self, mock_exchange):
        mock_exchange.ohlcv_data['BTC/USDT'] = Exception("Exchange unavailable")
        signal = cerebro.consultar_senal_mercado(mock_exchange, 'BTC/USDT')
        assert signal == {'entrar': False}

    def test_bullish_signal_generates_long(self, mock_exchange, make_synthetic_bars):
        mock_exchange.ohlcv_data['BTC/USDT'] = make_synthetic_bars('bullish')
        cerebro.capital = 25.0

        signal = cerebro.consultar_senal_mercado(mock_exchange, 'BTC/USDT')
        assert signal['entrar'] is True
        assert signal['lado'] == 'long'
        assert signal['cantidad_usdt'] == 25.0

    def test_bearish_signal_generates_short(self, mock_exchange, make_synthetic_bars):
        mock_exchange.ohlcv_data['BTC/USDT'] = make_synthetic_bars('bearish')
        cerebro.capital = 30.0

        signal = cerebro.consultar_senal_mercado(mock_exchange, 'BTC/USDT')
        assert signal['entrar'] is True
        assert signal['lado'] == 'short'
        assert signal['cantidad_usdt'] == 30.0

    def test_lateral_flat_market_no_signal(self, mock_exchange, make_synthetic_bars):
        mock_exchange.ohlcv_data['BTC/USDT'] = make_synthetic_bars('lateral')

        signal = cerebro.consultar_senal_mercado(mock_exchange, 'BTC/USDT')
        assert signal == {'entrar': False}

    def test_pullback_market_no_signal(self, mock_exchange, make_synthetic_bars):
        mock_exchange.ohlcv_data['BTC/USDT'] = make_synthetic_bars('pullback')

        signal = cerebro.consultar_senal_mercado(mock_exchange, 'BTC/USDT')
        assert signal == {'entrar': False}

    def test_nan_values_handled_without_triggering_signal(self, mock_exchange, make_synthetic_bars):
        mock_exchange.ohlcv_data['BTC/USDT'] = make_synthetic_bars('nan')

        signal = cerebro.consultar_senal_mercado(mock_exchange, 'BTC/USDT')
        assert signal == {'entrar': False}

    @pytest.mark.xfail(
        strict=True,
        reason="consultar_senal_mercado crashes with TypeError or IndexError on insufficient bars instead of returning {'entrar': False}"
    )
    def test_insufficient_bars_handled_gracefully(self, mock_exchange, make_synthetic_bars):
        mock_exchange.ohlcv_data['BTC/USDT'] = make_synthetic_bars('insufficient')
        signal = cerebro.consultar_senal_mercado(mock_exchange, 'BTC/USDT')
        assert signal == {'entrar': False}

    @pytest.mark.xfail(
        strict=True,
        reason="consultar_senal_mercado crashes with IndexError on empty bars instead of returning {'entrar': False}"
    )
    def test_empty_bars_handled_gracefully(self, mock_exchange, make_synthetic_bars):
        mock_exchange.ohlcv_data['BTC/USDT'] = make_synthetic_bars('empty')
        signal = cerebro.consultar_senal_mercado(mock_exchange, 'BTC/USDT')
        assert signal == {'entrar': False}

    @pytest.mark.xfail(
        strict=True,
        reason="RSI filter (rsi_reciente_bajo) is commented out in cerebro.py lines 70 and 77"
    )
    def test_long_signal_rejected_when_rsi_filter_not_met(self, mock_exchange, make_synthetic_bars):
        # In a valid bullish scenario, price > SMA200 and MACDh > 0.
        # But if RSI was never oversold (< 40 in past 3 bars), the strategy specification
        # (SMA200 + MACD + RSI) should reject the signal.
        # Production has `# rsi_reciente_bajo and` commented out, so it accepts regardless of RSI.
        bars = make_synthetic_bars('bullish')
        mock_exchange.ohlcv_data['BTC/USDT'] = bars
        signal = cerebro.consultar_senal_mercado(mock_exchange, 'BTC/USDT')
        # If RSI filter were active, this would be rejected because RSI in this bullish run is ~100 (> 40).
        assert signal['entrar'] is False


class TestPosicionesActivas:
    """Tests for active positions management."""

    def test_registrar_entrada_initializes_position_correctly(self):
        cerebro.registrar_entrada('BTC/USDT', 50000.0, 'long', leverage=10)

        assert 'BTC/USDT' in cerebro.posiciones_activas
        pos = cerebro.posiciones_activas['BTC/USDT']
        assert pos['entry_price'] == 50000.0
        assert pos['highest_pnl'] == -100
        assert pos['side'] == 'long'
        assert pos['leverage'] == 10

    def test_registrar_entrada_default_leverage(self):
        cerebro.registrar_entrada('ETH/USDT', 3000.0, 'short')

        pos = cerebro.posiciones_activas['ETH/USDT']
        assert pos['leverage'] == 1
        assert pos['side'] == 'short'

    def test_monitorear_posicion_unknown_symbol_returns_nada(self):
        res = cerebro.monitorear_posicion('UNKNOWN/USDT', 100.0)
        assert res == {'accion': 'NADA'}


class TestTrailingStop:
    """Tests for Trailing Stop logic: activates at >15% ROI, closes if drops >=5% from peak."""

    def test_trailing_stop_long_full_lifecycle(self):
        # Entry at 100.0 with 10x leverage
        cerebro.registrar_entrada('BTC/USDT', 100.0, 'long', leverage=10)

        # 1. Price moves to 101.0 -> ROI = +10% (below activation threshold of 15%)
        res1 = cerebro.monitorear_posicion('BTC/USDT', 101.0)
        assert res1['accion'] == 'MANTENER'
        assert cerebro.posiciones_activas['BTC/USDT']['highest_pnl'] == 10.0

        # 2. Price moves to 102.0 -> ROI = +20% (activates trailing stop)
        res2 = cerebro.monitorear_posicion('BTC/USDT', 102.0)
        assert res2['accion'] == 'MANTENER'
        assert cerebro.posiciones_activas['BTC/USDT']['highest_pnl'] == 20.0

        # 3. Small retracement: price drops to 101.8 -> ROI = +18% (retroceso = 20 - 18 = 2% < 5%)
        res3 = cerebro.monitorear_posicion('BTC/USDT', 101.8)
        assert res3['accion'] == 'MANTENER'
        assert cerebro.posiciones_activas['BTC/USDT']['highest_pnl'] == 20.0

        # 4. Larger retracement: price drops to 101.5 -> ROI = +15% (retroceso = 20 - 15 = 5% >= 5%)
        # Should trigger close!
        res4 = cerebro.monitorear_posicion('BTC/USDT', 101.5)
        assert res4['accion'] == 'CERRAR'
        assert res4['lado'] == 'long'
        assert 'BTC/USDT' not in cerebro.posiciones_activas

    def test_trailing_stop_short_full_lifecycle(self):
        # Entry at 100.0 with 5x leverage
        cerebro.registrar_entrada('ETH/USDT', 100.0, 'short', leverage=5)

        # 1. Price falls to 96.0 -> ROI = -(96 - 100) / 100 * 100 * 5 = +20% (activated)
        res1 = cerebro.monitorear_posicion('ETH/USDT', 96.0)
        assert res1['accion'] == 'MANTENER'
        assert cerebro.posiciones_activas['ETH/USDT']['highest_pnl'] == 20.0

        # 2. Price retraces up to 97.2 -> ROI = -(97.2 - 100) / 100 * 100 * 5 = +14%
        # Retroceso = 20 - 14 = 6% >= 5% -> close
        res2 = cerebro.monitorear_posicion('ETH/USDT', 97.2)
        assert res2['accion'] == 'CERRAR'
        assert res2['lado'] == 'short'
        assert 'ETH/USDT' not in cerebro.posiciones_activas

    def test_trailing_stop_does_not_activate_if_peak_below_15_percent(self):
        cerebro.registrar_entrada('SOL/USDT', 100.0, 'long', leverage=1)

        # Peak at +10% ROI
        cerebro.monitorear_posicion('SOL/USDT', 110.0)
        # Price drops to 104.0 (drop of 6% from peak, but peak 10% < 15%)
        res = cerebro.monitorear_posicion('SOL/USDT', 104.0)

        assert res['accion'] == 'MANTENER'
        assert 'SOL/USDT' in cerebro.posiciones_activas

    def test_trailing_stop_tracks_successive_higher_peaks(self):
        cerebro.registrar_entrada('BTC/USDT', 100.0, 'long', leverage=1)

        # Peak 1: 18%
        cerebro.monitorear_posicion('BTC/USDT', 118.0)
        assert cerebro.posiciones_activas['BTC/USDT']['highest_pnl'] == 18.0

        # Peak 2: 25%
        cerebro.monitorear_posicion('BTC/USDT', 125.0)
        assert cerebro.posiciones_activas['BTC/USDT']['highest_pnl'] == 25.0

        # Drop to 121.0 -> ROI = 21% (retroceso = 25 - 21 = 4% < 5%) -> MANTENER
        res_hold = cerebro.monitorear_posicion('BTC/USDT', 121.0)
        assert res_hold['accion'] == 'MANTENER'

        # Drop to 119.5 -> ROI = 19.5% (retroceso = 25 - 19.5 = 5.5% >= 5%) -> CERRAR
        res_close = cerebro.monitorear_posicion('BTC/USDT', 119.5)
        assert res_close['accion'] == 'CERRAR'

    @pytest.mark.xfail(
        strict=True,
        reason="monitorear_posicion uses strictly > 15 for activation, so exact 15.0% peak does not activate trailing stop"
    )
    def test_trailing_stop_activates_at_exact_15_percent_boundary(self):
        # If strategy activates at +15%, exact 15.0% peak followed by 5% drop should close
        cerebro.registrar_entrada('BTC/USDT', 100.0, 'long', leverage=1)
        cerebro.monitorear_posicion('BTC/USDT', 115.0)  # exact 15.0%
        res = cerebro.monitorear_posicion('BTC/USDT', 109.0)  # 9% ROI, drop = 6% >= 5%
        assert res['accion'] == 'CERRAR'


class TestHardStop:
    """Tests for Hard Stop logic: closes when ROI < -15%."""

    def test_hard_stop_long_triggers_when_roi_below_minus_15(self):
        cerebro.registrar_entrada('BTC/USDT', 100.0, 'long', leverage=1)

        # Price drops to 84.0 -> ROI = -16% (< -15%)
        res = cerebro.monitorear_posicion('BTC/USDT', 84.0)

        assert res['accion'] == 'CERRAR'
        assert res['lado'] == 'long'
        assert 'BTC/USDT' not in cerebro.posiciones_activas

    def test_hard_stop_short_triggers_when_roi_below_minus_15(self):
        cerebro.registrar_entrada('ETH/USDT', 100.0, 'short', leverage=2)

        # Price rises to 108.0 -> ROI = -(108 - 100) / 100 * 100 * 2 = -16% (< -15%)
        res = cerebro.monitorear_posicion('ETH/USDT', 108.0)

        assert res['accion'] == 'CERRAR'
        assert res['lado'] == 'short'
        assert 'ETH/USDT' not in cerebro.posiciones_activas

    def test_hard_stop_exact_minus_15_does_not_trigger(self):
        # Strict inequality < -15: exact -15.0% should MANTENER
        cerebro.registrar_entrada('SOL/USDT', 100.0, 'long', leverage=1)

        res = cerebro.monitorear_posicion('SOL/USDT', 85.0)  # ROI = -15.0%

        assert res['accion'] == 'MANTENER'
        assert 'SOL/USDT' in cerebro.posiciones_activas

    def test_hard_stop_at_minus_15_point_01_triggers(self):
        cerebro.registrar_entrada('SOL/USDT', 100.0, 'long', leverage=1)

        res = cerebro.monitorear_posicion('SOL/USDT', 84.99)  # ROI = -15.01% < -15%

        assert res['accion'] == 'CERRAR'
        assert 'SOL/USDT' not in cerebro.posiciones_activas


class TestCerebroEdgeCasesAndBugs:
    """Tests for edge cases and documented production bugs."""

    @pytest.mark.xfail(
        strict=True,
        reason="monitorear_posicion returns string 'NADA' instead of dict {'accion': 'NADA'} when entry_price is 0"
    )
    def test_monitorear_posicion_entry_zero_returns_dict(self):
        cerebro.registrar_entrada('BTC/USDT', 0.0, 'long', leverage=1)
        res = cerebro.monitorear_posicion('BTC/USDT', 100.0)

        # Should return a dictionary with 'accion': 'NADA' to prevent TypeError in callers
        assert isinstance(res, dict)
        assert res.get('accion') == 'NADA'
