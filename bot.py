import os
import requests

FMP_API_KEY = os.getenv("FMP_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

TOP_ACTIONS = 10
TOP_ETFS = 10


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

    print("Telegram:", r.status_code)
    print(r.text)


def get_actions():

    url = "https://financialmodelingprep.com/stable/company-screener"

    params = {
        "isEtf": "false",
        "isFund": "false",
        "marketCapMoreThan": 300000000,
        "marketCapLowerThan": 10000000000,
        "priceLowerThan": 50,
        "volumeMoreThan": 300000,
        "isActivelyTrading": "true",
        "limit": 100,
        "apikey": FMP_API_KEY
    }

    r = requests.get(url, params=params, timeout=30)

    print("Actions status:", r.status_code)

    r.raise_for_status()

    return r.json()


def get_etfs():

    url = "https://financialmodelingprep.com/stable/company-screener"

    params = {
        "isEtf": "true",
        "isActivelyTrading": "true",
        "volumeMoreThan": 10000,
        "limit": 100,
        "apikey": FMP_API_KEY
    }

    r = requests.get(url, params=params, timeout=30)

    print("ETF status:", r.status_code)

    r.raise_for_status()

    return r.json()


def score_action(item):

    score = 0

    market_cap = item.get("marketCap") or 0
    volume = item.get("volume") or 0
    price = item.get("price") or 0

    if market_cap < 1000000000:
        score += 40
    elif market_cap < 5000000000:
        score += 30
    else:
        score += 15

    score += min(volume / 1000000, 1) * 40

    if price < 20:
        score += 20
    elif price < 50:
        score += 10

    return round(score, 1)


def score_etf(item):

    score = 0

    volume = item.get("volume") or 0
    price = item.get("price") or 0

    score += min(volume / 500000, 1) * 60

    if 20 <= price <= 300:
        score += 40
    else:
        score += 20

    return round(score, 1)


def main():

    if not FMP_API_KEY:
        raise Exception("FMP_API_KEY manquant")

    actions = get_actions()
    etfs = get_etfs()

    scored_actions = []

    for item in actions:

        scored_actions.append({
            "symbol": item.get("symbol"),
            "name": item.get("companyName"),
            "price": item.get("price"),
            "volume": item.get("volume"),
            "score": score_action(item)
        })

    scored_etfs = []

    for item in etfs:

        scored_etfs.append({
            "symbol": item.get("symbol"),
            "name": item.get("companyName"),
            "price": item.get("price"),
            "volume": item.get("volume"),
            "score": score_etf(item)
        })

    scored_actions = sorted(
        scored_actions,
        key=lambda x: x["score"],
        reverse=True
    )[:TOP_ACTIONS]

    scored_etfs = sorted(
        scored_etfs,
        key=lambda x: x["score"],
        reverse=True
    )[:TOP_ETFS]

    message = "🚀 AnisTrade\n\n"

    message += "📈 ACTIONS\n\n"

    for i, item in enumerate(scored_actions, start=1):

        message += (
            f"{i}. {item['symbol']}\n"
            f"Prix: {item['price']}$\n"
            f"Score: {item['score']}\n\n"
        )

    message += "\n📊 ETF\n\n"

    for i, item in enumerate(scored_etfs, start=1):

        message += (
            f"{i}. {item['symbol']}\n"
            f"Prix: {item['price']}$\n"
            f"Score: {item['score']}\n\n"
        )

    send_telegram(message)


if __name__ == "__main__":
    main()
