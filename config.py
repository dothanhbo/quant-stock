import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN = os.getenv("8860199022:AAHNtR2Xd5eekkzRvG_ILrslvrc4pKNwd2I")
CHAT_ID = os.getenv("5137019839")

SCAN_TIME = "15:05"

DATABASE = "data/market.db"

RR_RATIO = 2.0

EMA_FAST = 10
EMA_MID = 20
EMA_SLOW = 50

RSI_MIN = 50
RSI_MAX = 68

VOL_FACTOR = 1.5