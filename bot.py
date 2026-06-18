import os
import requests

FMP_API_KEY = os.getenv("FMP_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

MIN_MARKET_CAP = 300_000_000
MAX_MARKET_CAP = 8_000_000_000
MIN_VOLUME = 300_000
MAX_PRICE = 50
TOP_N = 10


def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    response = requests.post(
        url,
        json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=20,
    )
    print(response.status_code)
    print(response.text)
    response.raise_for_status()


def fmp_stock_screener():
    url = "https://financialmodelingprep.com/api/v3/stock-screener"
    params = {
        "marketCapMoreThan": MIN_MARKET_CAP,
        "marketCapLowerThan": MAX_MARKET_CAP,
        "volumeMoreThan": MIN_VOLUME,
        "priceLowerThan": MAX_PRICE,
        "isActivelyTrading": "true",
        "limit": 100,
        "apikey": FMP_API_KEY,
    }

    response = requests.get(url, params=params, timeout=30)
    print("FMP screener:", response.status_code)
    response.raise_for_status()
    return response.json()


def fmp_price_target(symbol):
    url = "https://financialmodelingprep.com/api/v4/price-target-summary"
    params = {
        "symbol": symbol,
        "apikey": FMP_API_KEY,
    }

    try:
        response = requests.get(url, params=params, timeout=20)
        if response.status_code != 200:
            return None

        data = response.json()
        if isinstance(data, list) and len(data) > 0:
            return data[0]

        if isinstance(data, dict):
            return data

        return None
    except Exception as e:
        print(f"Erreur price target {symbol}: {e}")
        return None


def compute_upside(price, target):
    if not price or not target or price <= 0:
        return None
    return ((target - price) / price) * 100


def score_stock(stock, target_data):
    price = stock.get("price")
    market_cap = stock.get("marketCap") or 0
    volume = stock.get("volume") or 0

    target = None
    if target_data:
        target = (
            target_data.get("targetConsensus")
            or target_data.get("targetMean")
            or target_data.get("priceTarget")
            or target_data.get("target")
        )

    upside = compute_upside(price, target)

    if upside is None:
        return 0, None, target

    upside_score = min(max(upside, 0), 150) / 150 * 60
    volume_score = min(volume / 1_000_000, 1) * 15

    if market_cap < 1_000_000_000:
        cap_score = 15
    elif market_cap < 5_000_000_000:
        cap_score = 12
    else:
        cap_score = 8

    price_score = 10 if price and price < 20 else 6

    total_score = upside_score + volume_score + cap_score + price_score

    return round(min(total_score, 100), 1), round(upside, 1), target


def format_market_cap(value):
    if not value:
        return "N/A"
    if value >= 1_000_000_000:
        return f"{value / 1_000_000_000:.1f} Md$"
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f} M$"
    return str(value)


def main():
    if not FMP_API_KEY:
        raise Exception("FMP_API_KEY manquant")
    if not TELEGRAM_TOKEN:
        raise Exception("TELEGRAM_TOKEN manquant")
    if not TELEGRAM_CHAT_ID:
        raise Exception("TELEGRAM_CHAT_ID manquant")

    stocks = fmp_stock_screener()
    results = []

    for stock in stocks:
        symbol = stock.get("symbol")
        if not symbol:
            continue

        target_data = fmp_price_target(symbol)
        score, upside, target = score_stock(stock, target_data)

        if upside is not None and upside >= 70 and score >= 40:
            results.append({
                "symbol": symbol,
                "name": stock.get("companyName"),
                "price": stock.get("price"),
                "target": target,
                "upside": upside,
                "score": score,
                "marketCap": stock.get("marketCap"),
                "volume": stock.get("volume"),
                "exchange": stock.get("exchangeShortName"),
            })

    results = sorted(results, key=lambda x: x["score"], reverse=True)[:TOP_N]

    message = "🚀 <b>AnisTrade - Top actions haut potentiel</b>\n\n"

    if not results:
        message += "Aucun signal fort détecté aujourd’hui.\n"
    else:
        for i, r in enumerate(results, 1):
            message += (
                f"<b>{i}. {r['symbol']} - {r['name']}</b>\n"
                f"Prix: {r['price']}$ | Target: {r['target']}$\n"
                f"Potentiel théorique: +{r['upside']}%\n"
                f"Score AnisTrade: {r['score']}/100\n"
                f"Market cap: {format_market_cap(r['marketCap'])}\n"
                f"Volume: {r['volume']}\n"
                f"Exchange: {r['exchange']}\n\n"
            )

    message += "⚠️ Signal indicatif. Ce n’est pas un conseil financier."

    send_telegram(message)


if __name__ == "__main__":
    main()
