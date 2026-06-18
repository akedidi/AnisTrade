  import os
import requests

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

message = """
🚀 AnisTrade

Le bot fonctionne correctement.

✅ GitHub Actions
✅ Telegram

Prochaine étape :
- FMP
- Finnhub
- Détection des actions à fort potentiel
- Alertes automatiques

Version : 0.1
"""

url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

response = requests.post(
    url,
    json={
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message
    }
)

print(response.text)
