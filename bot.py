import os
import time
import random
import requests

FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

TOP_ACTIONS = 10
TOP_ETFS = 10
MAX_SYMBOLS_TO_SCAN = 120

FINNHUB_BASE = "https://finnhub.io/api/v1"


def finnhub_get(endpoint, params=None):
    if params is None:
        params = {}

    params["token"] = FINNHUB_API_KEY
    url = f"{FINNHUB_BASE}/{endpoint}"

    r = requests.get(url, params=params, timeout=20)
    print(endpoint, r.status_code)

    if r.status_code != 200:
        print(r.text[:500])
        return None

    return r.json()


def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    r = requests.post(
        url,
        json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "disable_web_page_preview": True
        },
        timeout=20
    )

    print("Telegram:", r.status_code)
    print(r.text[:500])


def get_all_us_symbols():
    data = finnhub_get("stock/symbol", {"exchange": "US"})

    if not isinstance(data, list):
        return [], []

    actions = []
    etfs = []

    for item in data:
        symbol = item.get("symbol")
        description = item.get("description", "")
        security_type = (item.get("type") or "").upper()

        if not symbol:
            continue

        if "." in symbol or "-" in symbol:
            continue

        if security_type == "ETF":
            etfs.append({
                "symbol": symbol,
                "name": description
            })

        elif security_type in ["COMMON STOCK", "ADR", "REIT"]:
            actions.append({
                "symbol": symbol,
                "name": description
            })

    random.shuffle(actions)
    random.shuffle(etfs)

    return actions[:MAX_SYMBOLS_TO_SCAN], etfs[:MAX_SYMBOLS_TO_SCAN]


def get_quote(symbol):
    return finnhub_get("quote", {"symbol": symbol})


def score_item(symbol, name, quote, is_etf=False):
    if not quote:
        return None

    current = quote.get("c") or 0
    high = quote.get("h") or 0
    low = quote.get("l") or 0
    open_price = quote.get("o") or 0
    previous_close = quote.get("pc") or 0

    if current <= 0 or previous_close <= 0:
        return None

    daily_change = ((current - previous_close) / previous_close) * 100

    intraday_range = 0
    if low > 0:
        intraday_range = ((high - low) / low) * 100

    score = 0

    # Momentum positif mais pas délirant
    if 0 < daily_change <= 8:
        score += 45
    elif 8 < daily_change <= 15:
        score += 25
    elif -3 <= daily_change <= 0:
        score += 20

    # Volatilité utile
    if 2 <= intraday_range <= 12:
        score += 25
    elif 12 < intraday_range <= 20:
        score += 15

    # Prix accessible
    if not is_etf:
        if current < 10:
            score += 20
        elif current < 50:
            score += 15
        elif current < 100:
            score += 5
    else:
        if 20 <= current <= 500:
            score += 20
        else:
            score += 10

    # Bonus ETF plus stable
    if is_etf:
        score += 10

    return {
        "symbol": symbol,
        "name": name[:45],
        "price": round(current, 2),
        "change": round(daily_change, 2),
        "range": round(intraday_range, 2),
        "score": round(score, 1)
    }


def scan(items, is_etf=False):
    results = []

    for item in items:
        symbol = item["symbol"]
        name = item["name"]

        quote = get_quote(symbol)
        scored = score_item(symbol, name, quote, is_etf=is_etf)

        if scored:
            results.append(scored)

        time.sleep(1.1)  # respecte la limite gratuite Finnhub ~60 appels/min

    results = sorted(results, key=lambda x: x["score"], reverse=True)

    return results


def format_section(title, items):
    text = f"{title}\n\n"

    if not items:
        return text + "Aucun résultat.\n\n"

    for i, item in enumerate(items, start=1):
        text += (
            f"{i}. {item['symbol']} - {item['name']}\n"
            f"Prix: {item['price']}$ | Variation: {item['change']}%\n"
            f"Range jour: {item['range']}% | Score: {item['score']}/100\n\n"
        )

    return text


def main():
    if not FINNHUB_API_KEY:
        raise Exception("FINNHUB_API_KEY manquant")
    if not TELEGRAM_TOKEN:
        raise Exception("TELEGRAM_TOKEN manquant")
    if not TELEGRAM_CHAT_ID:
        raise Exception("TELEGRAM_CHAT_ID manquant")

    actions, etfs = get_all_us_symbols()

    print("Actions à scanner:", len(actions))
    print("ETF à scanner:", len(etfs))

    scored_actions = scan(actions, is_etf=False)[:TOP_ACTIONS]
    scored_etfs = scan(etfs, is_etf=True)[:TOP_ETFS]

    message = "🚀 AnisTrade - Scan générique Finnhub\n\n"
    message += format_section("📈 ACTIONS", scored_actions)
    message += format_section("📊 ETF", scored_etfs)
    message += "⚠️ Signal indicatif, pas un conseil financier."

    # Telegram limite les messages à ~4096 caractères
    send_telegram(message[:3900])


if __name__ == "__main__":
    main()
