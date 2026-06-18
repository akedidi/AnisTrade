import os
import requests

FMP_API_KEY = os.getenv("FMP_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

TOP_N = 10


def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    response = requests.post(
        url,
        json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message
        },
        timeout=20
    )

    print(response.status_code)
    print(response.text)


def get_candidates():
    url = "https://financialmodelingprep.com/stable/company-screener"

    params = {
        "marketCapMoreThan": 300000000,
        "marketCapLowerThan": 10000000000,
        "volumeMoreThan": 300000,
        "priceLowerThan": 50,
        "isActivelyTrading": True,
        "limit": 50,
        "apikey": FMP_API_KEY
    }

    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()

    return r.json()


def get_price_target(symbol):
    url = "https://financialmodelingprep.com/stable/price-target-consensus"

    params = {
        "symbol": symbol,
        "apikey": FMP_API_KEY
    }

    try:
        r = requests.get(url, params=params, timeout=20)

        if r.status_code != 200:
            return None

        data = r.json()

        if isinstance(data, list) and len(data) > 0:
            return data[0]

        if isinstance(data, dict):
            return data

        return None

    except Exception:
        return None


def compute_score(price, consensus_target, market_cap):
    if not price or not consensus_target:
        return None

    upside = ((consensus_target - price) / price) * 100

    score = 0

    score += min(max(upside, 0), 150) * 0.5

    if market_cap < 1000000000:
        score += 20
    elif market_cap < 5000000000:
        score += 15
    else:
        score += 10

    return round(score, 1), round(upside, 1)


def main():

    if not FMP_API_KEY:
        raise Exception("FMP_API_KEY manquant")

    candidates = get_candidates()

    results = []

    for stock in candidates:

        symbol = stock.get("symbol")
        price = stock.get("price")
        market_cap = stock.get("marketCap", 0)

        if not symbol or not price:
            continue

        target_data = get_price_target(symbol)

        if not target_data:
            continue

        consensus_target = (
            target_data.get("targetConsensus")
            or target_data.get("consensusTarget")
            or target_data.get("priceTarget")
        )

        if not consensus_target:
            continue

        score_data = compute_score(
            price,
            consensus_target,
            market_cap
        )

        if not score_data:
            continue

        score, upside = score_data

        if upside < 50:
            continue

        results.append({
            "symbol": symbol,
            "price": price,
            "target": consensus_target,
            "upside": upside,
            "score": score
        })

    results = sorted(
        results,
        key=lambda x: x["score"],
        reverse=True
    )[:TOP_N]

    message = "🚀 AnisTrade V1\n\n"

    if not results:
        message += "Aucune opportunité détectée."
    else:
        for i, r in enumerate(results, start=1):
            message += (
                f"{i}. {r['symbol']}\n"
                f"Prix : {r['price']}$\n"
                f"Target : {r['target']}$\n"
                f"Potentiel : +{r['upside']}%\n"
                f"Score : {r['score']}\n\n"
            )

    send_telegram(message)


if __name__ == "__main__":
    main()
