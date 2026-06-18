import os
import time
import random
import requests
import warnings
import pandas as pd
import yfinance as yf

# Désactiver les avertissements liés aux futures versions de pandas
warnings.simplefilter(action='ignore', category=FutureWarning)

FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(
            url,
            json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown", "disable_web_page_preview": True},
            timeout=15
        )
    except Exception as e:
        print(f"Erreur Telegram: {e}")

def get_dynamic_universe(max_actions=800, max_etfs=200):
    print("📥 Récupération de l'annuaire complet du marché US via Finnhub...")
    url = f"https://finnhub.io/api/v1/stock/symbol?exchange=US&token={FINNHUB_API_KEY}"
    
    try:
        r = requests.get(url, timeout=15)
        if r.status_code != 200:
            print(f"Erreur Finnhub Code {r.status_code}")
            return [], []
        data = r.json()
    except Exception as e:
        print(f"Erreur de connexion Finnhub: {e}")
        return [], []

    actions = []
    etfs = []

    for item in data:
        symbol = item.get("symbol", "")
        sec_type = item.get("type", "").upper()

        # Exclure les warrants, actions préférentielles et symboles invalides pour Yahoo
        if "." in symbol or "-" in symbol or not symbol:
            continue

        if sec_type == "ETF":
            etfs.append(symbol)
        elif sec_type in ["COMMON STOCK", "ADR", "REIT"]:
            actions.append(symbol)

    print(f"🌍 Marché total trouvé : {len(actions)} Actions et {len(etfs)} ETF.")
    
    # Mélange aléatoire pour échantillonner une zone différente du marché chaque jour
    random.shuffle(actions)
    random.shuffle(etfs)

    # Coupure stricte pour éviter le Rate Limiting (Blocage IP) par Yahoo Finance
    final_actions = actions[:max_actions]
    final_etfs = etfs[:max_etfs]
    
    print(f"🎯 Échantillon du jour : {len(final_actions)} Actions et {len(final_etfs)} ETF.")
    return final_actions, final_etfs

def analyze_group(tickers, is_etf=False):
    if not tickers:
        return pd.DataFrame()
        
    type_name = 'ETF' if is_etf else 'Actions'
    print(f"📡 Téléchargement vectoriel de {len(tickers)} {type_name}...")
    
    # Téléchargement massif optimisé (Désactivation des messages d'erreur de Yahoo pour les tickers radiés)
    data = yf.download(tickers, period="5d", progress=False, ignore_tz=True)
    
    # Vérification de la structure des données (Cas où un seul ticker est valide)
    if 'Close' not in data or 'Volume' not in data:
        return pd.DataFrame()
        
    closes = data['Close']
    volumes = data['Volume']

    results = []

    # Parcours des colonnes (Tickers)
    # yfinance renvoie des DataFrames avec les tickers en colonnes
    if isinstance(closes, pd.Series):
        closes = closes.to_frame(name=tickers[0])
        volumes = volumes.to_frame(name=tickers[0])

    for ticker in closes.columns:
        try:
            # Nettoyage des jours fériés ou non cotés (NaN)
            c = closes[ticker].dropna()
            v = volumes[ticker].dropna()

            if len(c) < 2 or len(v) < 2:
                continue

            today_close = float(c.iloc[-1])
            yesterday_close = float(c.iloc[-2])
            today_vol = float(v.iloc[-1])
            
            # Calcul du volume moyen sur la période (5 jours)
            avg_vol = float(v.mean())

            if yesterday_close == 0 or avg_vol == 0:
                continue

            pct_change = ((today_close - yesterday_close) / yesterday_close) * 100
            vol_ratio = today_vol / avg_vol

            # ⚙️ MOTEUR QUANTITATIF : Filtres différenciés
            if is_etf:
                # Les ETF sont denses : on déclenche à +2.5% et volume x2.0
                if pct_change >= 2.5 and vol_ratio >= 2.0:
                    results.append({'Ticker': ticker, 'Prix': today_close, 'Var': pct_change, 'VolRatio': vol_ratio})
            else:
                # Les actions : on déclenche à +5%, volume x2.5, et on ignore les Penny Stocks (<2$)
                if pct_change >= 5.0 and vol_ratio >= 2.5 and today_close >= 2.0:
                    results.append({'Ticker': ticker, 'Prix': today_close, 'Var': pct_change, 'VolRatio': vol_ratio})

        except Exception:
            continue

    df_results = pd.DataFrame(results)
    if not df_results.empty:
        df_results = df_results.sort_values(by='VolRatio', ascending=False)
    
    return df_results

def main():
    if not all([FINNHUB_API_KEY, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID]):
        raise Exception("Clés d'API ou Telegram manquantes. Vérifiez vos Secrets GitHub.")

    # 1. Cartographie du marché
    stocks, etfs = get_dynamic_universe(max_actions=800, max_etfs=200)

    # 2. Analyse vectorielle
    df_stocks = analyze_group(stocks, is_etf=False)
    df_etfs = analyze_group(etfs, is_etf=True)

    # 3. Formatage et envoi Telegram
    if not df_stocks.empty or not df_etfs.empty:
        message = "🚨 *AnisTrade - DÉTECTION GÉNÉRIQUE*\n\n"
        
        if not df_stocks.empty:
            message += "📈 *ACTIONS (Filtre > 5%)*\n"
            # Limite à 8 pour ne pas spammer
            for _, row in df_stocks.head(8).iterrows():
                message += f"🔥 *{row['Ticker']}* : +{row['Var']:.1f}% | Vol: {row['VolRatio']:.1f}x | {row['Prix']:.2f}$\n"
            message += "\n"
            
        if not df_etfs.empty:
            message += "📊 *ETF (Filtre > 2.5%)*\n"
            # Limite à 5
            for _, row in df_etfs.head(5).iterrows():
                message += f"⚡ *{row['Ticker']}* : +{row['Var']:.1f}% | Vol: {row['VolRatio']:.1f}x | {row['Prix']:.2f}$\n"
            message += "\n"
            
        message += "⚠️ _Anomalies confirmées. Scannez le marché des options._"
        send_telegram(message)
        print("🚀 Alerte quant envoyée avec succès !")
    else:
        print("Calme plat. Aucune anomalie détectée sur l'échantillon.")

if __name__ == "__main__":
    main()
