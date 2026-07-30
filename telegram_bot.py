import requests

from config import TELEGRAM_TOKEN
from config import CHAT_ID

def send(message):

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    requests.post(
        url,
        json={
            "chat_id": CHAT_ID,
            "text": message,
            "parse_mode": "Markdown"
        },
        timeout=20
    )