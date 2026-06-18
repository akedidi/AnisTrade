import os
import requests
import traceback

FMP_API_KEY = os.getenv("FMP_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    r = requests.post(
        url,
        json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message
        },
        timeout=20
    )

    print("Telegram status:", r.status_code)
    print("Telegram response:", r.text)

def main():

    print("=== DEBUG ===")
    print("FMP_API_KEY:", "OK" if FMP_API_KEY else "MISSING")
    print("TELEGRAM_TOKEN:", "OK" if TELEGRAM_TOKEN else "MISSING")
    print("TELEGRAM_CHAT_ID:", "OK" if TELEGRAM_CHAT_ID else "MISSING")
    print("==============")

    # Test FMP Stable Screener
    url = "https://financialmodelingprep.com/stable/company-screener"

    params = {
        "limit": 5,
        "apikey": FMP_API_KEY
    }

    r = requests.get(url, params=params, timeout=30)

    print("FMP STATUS:", r.status_code)
    print("FMP BODY:")
    print(r.text[:1000])

    send_telegram(
        f"🚀 AnisTrade\n\n"
        f"FMP Status={r.status_code}\n"
        f"Test terminé."
    )

if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("===== ERREUR =====")
        traceback.print_exc()
        raise
