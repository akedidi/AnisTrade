import os
import requests

FMP_API_KEY = os.getenv("FMP_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

print("=== DEBUG ===")
print("FMP_API_KEY:", "OK" if FMP_API_KEY else "MISSING")
print("TELEGRAM_TOKEN:", "OK" if TELEGRAM_TOKEN else "MISSING")
print("TELEGRAM_CHAT_ID:", "OK" if TELEGRAM_CHAT_ID else "MISSING")
print("=============")

if not FMP_API_KEY:
    raise Exception("FMP_API_KEY manquant")

if not TELEGRAM_TOKEN:
    raise Exception("TELEGRAM_TOKEN manquant")

if not TELEGRAM_CHAT_ID:
    raise Exception("TELEGRAM_CHAT_ID manquant")

# Test FMP
url = "https://financialmodelingprep.com/api/v3/quote/AAPL"
response = requests.get(
    url,
    params={"apikey": FMP_API_KEY},
    timeout=20
)

print("FMP Status:", response.status_code)
print("FMP Response:", response.text[:300])

# Test Telegram
message = f"""
🚀 AnisTrade

FMP API OK : {response.status_code == 200}

Version Debug
"""

telegram_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

telegram_response = requests.post(
    telegram_url,
    json={
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message
    },
    timeout=20
)

print("Telegram Status:", telegram_response.status_code)
print("Telegram Response:", telegram_response.text)

telegram_response.raise_for_status()

print("SUCCESS")
