import os
from dotenv import load_dotenv
from bots.decision_bot.decision_bot import main_loop

load_dotenv()
TIMEFRAME = os.getenv("TIMEFRAME", "1h")
ATR_PERIOD = int(os.getenv("ATR_PERIOD", "14"))
RSI_PERIOD = int(os.getenv("RSI_PERIOD", "14"))
SMA_FAST = int(os.getenv("SMA_FAST", "30"))
SMA_TREND = int(os.getenv("SMA_TREND", "200"))
MACD_FAST = int(os.getenv("MACD_FAST", "12"))
MACD_SLOW = int(os.getenv("MACD_SLOW", "26"))
MACD_SIGNAL = int(os.getenv("MACD_SIGNAL", "9"))
LOOP_SLEEP_SEC = int(os.getenv("LOOP_SLEEP_SEC", "10"))
#SYMBOL = "ADA/USDT"

main_loop()