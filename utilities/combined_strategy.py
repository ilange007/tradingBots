import pandas as pd
from typing import Tuple

def get_combined_signal(df: pd.DataFrame, min_volatility_multiplier: float, max_volatility_multiplier: float) -> Tuple[str, int]:
    """
    Calcula una señal de trading combinada y su puntuación basada en el MACD, SMA, RSI, StochRSI y ATR.
    Esta versión utiliza un sistema de puntuación para generar señales.
    """
    if df.empty or len(df) < 200:
        return 'NEUTRAL', 0

    # Indicadores de tendencia y momentum
    current_close = df['close'].iloc[-1]
    current_sma_trend = df['SMA_TREND'].iloc[-1]
    
    # Cruces del MACD
    macd_line = df['MACD']
    macd_signal_line = df['MACD_SIGNAL']

    macd_crossover_up = (macd_line.iloc[-2] < macd_signal_line.iloc[-2]) and \
                        (macd_line.iloc[-1] > macd_signal_line.iloc[-1])
    
    macd_crossover_down = (macd_line.iloc[-2] > macd_signal_line.iloc[-2]) and \
                          (macd_line.iloc[-1] < macd_signal_line.iloc[-1])

    # Filtros de fuerza de movimiento
    current_rsi = df['RSI'].iloc[-1]
    current_stoch_rsi_k = df['STOCH_RSI_K'].iloc[-1]
    current_stoch_rsi_d = df['STOCH_RSI_D'].iloc[-1]

    stoch_rsi_crossover_up = (df['STOCH_RSI_K'].iloc[-1] > df['STOCH_RSI_D'].iloc[-1]) and \
                             (df['STOCH_RSI_K'].iloc[-2] < df['STOCH_RSI_D'].iloc[-2])

    stoch_rsi_crossover_down = (df['STOCH_RSI_K'].iloc[-1] < df['STOCH_RSI_D'].iloc[-1]) and \
                               (df['STOCH_RSI_K'].iloc[-2] > df['STOCH_RSI_D'].iloc[-2])
    
    # Nuevo filtro de volatilidad
    current_atr = df['ATR'].iloc[-1]
    atr_mean = df['ATR'].rolling(window=20).mean().iloc[-1]
    atr_volatility_filter_pass = (current_atr > atr_mean * min_volatility_multiplier) and \
                                 (current_atr < atr_mean * max_volatility_multiplier)

    if not atr_volatility_filter_pass:
        return 'NEUTRAL', 0

    # -----------------------------
    # Nuevo sistema de puntuación
    # -----------------------------
    buy_score = 0
    sell_score = 0

    # 1. Puntuación por tendencia (SMA)
    if current_close > current_sma_trend:
        buy_score += 1
    elif current_close < current_sma_trend:
        sell_score += 1
        
    # 2. Puntuación por MACD
    if macd_crossover_up:
        buy_score += 1
    elif macd_crossover_down:
        sell_score += 1
        
    # 3. Puntuación por RSI (sobreventa/sobrecompra)
    if current_rsi < 30:
        buy_score += 1
    elif current_rsi > 70:
        sell_score += 1
        
    # 4. Puntuación por StochRSI
    if stoch_rsi_crossover_up and current_stoch_rsi_k < 80:
        buy_score += 1
    elif stoch_rsi_crossover_down and current_stoch_rsi_k > 20:
        sell_score += 1
        
    # -----------------------------
    # Lógica para la señal final
    # -----------------------------
    
    # Define los umbrales (puedes ajustar estos valores)
    SIGNAL_THRESHOLD = 3
    
    if buy_score >= SIGNAL_THRESHOLD:
        return 'BUY', buy_score
    elif sell_score >= SIGNAL_THRESHOLD:
        return 'SELL', sell_score
    else:
        return 'NEUTRAL', 0

