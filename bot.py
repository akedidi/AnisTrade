import os
import time
import requests

FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

WATCHLIST = ["BHVN", "QURE", "XBI", "LABU", "IBB", "GILD", "AMGN", "BIIB", "VRTX", "REGN"]
FINNHUB_BASE = "https://finnhub.io/api/v1"

def finnhub_get(endpoint, params=None):
    if params is None:
        params = {}
    params["token"] = FINNHUB_API_KEY
    url = f"{FINNHUB_BASE}/{endpoint}"
    
    # Système de "Retry" pour absorber les blocages temporaires de la version gratuite
    max_retries = 3
    for attempt in range(max_retries):
        try:
            r = requests.get(url, params=params, timeout=15)
            if r.status_code == 200:
                return r.json()
            elif r.status_code == 429:
                print(f"⚠️ Limite API atteinte (429). Pause de 10s... (Essai {attempt + 1}/{max_retries})")
                time.sleep(10)
            else:
                print(f"Erreur API {endpoint} - Code: {r.status_code}")
                break
        except Exception as e:
            print(f"Erreur de connexion API {endpoint}: {e}")
            time.sleep(2)
            
    return None

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(
            url,
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True
            },
            timeout=15
        )
    except Exception as e:
        print(f"Erreur Telegram: {e}")

def check_volume_spike(symbol):
    quote = finnhub_get("quote", {"symbol": symbol})
    if not quote:
        return None
        
    current_price = quote.get("c", 0)
    previous_close = quote.get("pc", 0)
    
    # Protection stricte contre la division par zéro
    if previous_close == 0:
        print(f"Données de clôture indisponibles pour {symbol}.")
        return None
        
    daily_change = ((current_price - previous_close) / previous_close) * 100
    
    # Si le prix ne bouge pas assez, on économise un appel API sur le profil
    if daily_change < 5.0:
        return None

    # Récupération du profil uniquement si l'anomalie de prix est validée
    profile = finnhub_get("stock/profile2", {"symbol": symbol})
    
    # Gestion des profils introuvables (fréquent sur les small caps en API gratuite)
    if profile:
        market_cap = profile.get("marketCapitalization", 0)
        name = profile.get("name", symbol)
    else:
        market_cap = 0
        name = symbol

    # Filtre Quantitatif : Capitalisation < 50B$ ou inconnue (0)
    if market_cap < 50000:
        return {
            "symbol": symbol,
            "name": name,
            "price": current_price,
            "change": round(daily_change, 2),
            "cap": round(market_cap / 1000, 2) if market_cap > 0 else "Inconnue"
        }
        
    return None

def main():
    if not all([FINNHUB_API_KEY, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID]):
        raise Exception("Variables d'environnement manquantes.")

    alerts = []
    print(f"Début du scan quantitatif sur {len(WATCHLIST)} actifs...")

    for symbol in WATCHLIST:
        print(f"Analyse de {symbol}...")
        result = check_volume_spike(symbol)
        if result:
            alerts.append(result)
            
        # Temporisation dynamique pour la version gratuite
        time.sleep(1.5) 

    if alerts:
        message = "🚨 *AnisTrade - Détection d'Anomalie Institutionnelle*\n\n"
        for alert in alerts:
            cap_display = f"{alert['cap']}B$" if isinstance(alert['cap'], float) else alert['cap']
            message += (
                f"🔥 *{alert['symbol']}* - {alert['name']}\n"
                f" Prix : {alert['price']}$ | Mouvement : +{alert['change']}%\n"
                f" Capitalisation : {cap_display}\n\n"
            )
        message += "⚠️ _Vérifier immédiatement le ratio Vol/OI des options sur cet actif._"
        send_telegram(message)
        print("Alerte envoyée sur Telegram.")
    else:
        print("Aucune anomalie détectée.")

if __name__ == "__main__":
    main()
