import os
import re
import time
import logging
from datetime import datetime
from io import StringIO
import requests
import warnings
import pandas as pd
import yfinance as yf

warnings.simplefilter(action="ignore", category=FutureWarning)
logging.getLogger("yfinance").setLevel(logging.CRITICAL)

FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Bourses US majeures uniquement (exclut OTC / pink sheets)
MAJOR_MICS = {"XNYS", "XNAS", "ARCX", "BATS", "XASE"}
STOCK_TYPES = {"COMMON STOCK", "ADR", "REIT", "EQS"}
ETF_TYPES = {"ETF", "ETP", "ETN"}
SYMBOL_RE = re.compile(r"^[A-Z]{1,5}$")

# ETF majeurs (Finnhub ne tague plus type=ETF sur /stock/symbol)
CORE_ETFS = [
    "SPY", "QQQ", "IWM", "DIA", "VTI", "VOO", "IVV", "VEA", "VWO", "AGG",
    "BND", "TLT", "GLD", "SLV", "USO", "UNG", "XLK", "XLF", "XLE", "XLV",
    "XLI", "XLP", "XLY", "XLU", "XLB", "XLRE", "XLC", "XBI", "IBB", "ARKK",
    "ARKG", "ARKW", "ARKF", "ARKQ", "SMH", "SOXX", "IGV", "HACK", "TAN",
    "ICLN", "LIT", "GDX", "GDXJ", "XRT", "KRE", "KBE", "XHB", "ITB", "JETS",
    "XOP", "OIH", "EWJ", "EWG", "EWZ", "FXI", "EEM", "EFA", "VUG", "VTV",
    "SCHD", "VIG", "DVY", "HYG", "JNK", "LQD", "TIP", "SHY", "IEF", "RSP",
    "MTUM", "QUAL", "USMV", "SPLV", "BOTZ", "ROBO", "CIBR", "SKYY", "FINX",
    "XME", "PICK", "COPX", "REMX", "URA", "NLR", "XAR", "ITA", "PPA", "IYT",
    "IYR", "VNQ", "SCHH", "REM", "MORT", "EMB", "VTEB", "MUB", "PFF", "PGX",
]

CHUNK_SIZE = 80
CHUNK_DELAY_SEC = 0.4

# Options — scan uniquement sur les finalistes momentum
OPTIONS_MAX_EXPIRATIONS = 2
OPTIONS_MIN_VOL_OI = 2.0
OPTIONS_MIN_VOLUME_STOCK = 100
OPTIONS_MIN_VOLUME_ETF = 500
OPTIONS_DELAY_SEC = 0.25


def to_yahoo_symbol(symbol: str) -> str:
    return symbol.replace(".", "-")


def from_yahoo_symbol(symbol: str) -> str:
    return symbol.replace("-", ".")


def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(
            url,
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            },
            timeout=15,
        )
    except Exception as e:
        print(f"Erreur Telegram: {e}")


def fetch_index_tickers():
    """Indices de référence via Wikipedia — 0 appel API payant, toujours liquides."""
    stocks = set()
    headers = {"User-Agent": "AnisTradeBot/2.0 (momentum-scanner)"}
    for url, col in [
        ("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", "Symbol"),
        ("https://en.wikipedia.org/wiki/Nasdaq-100", "Ticker"),
    ]:
        try:
            r = requests.get(url, headers=headers, timeout=20)
            r.raise_for_status()
            tables = pd.read_html(StringIO(r.text))
            for table in tables:
                if col in table.columns:
                    for sym in table[col].dropna().astype(str):
                        sym = sym.strip().upper()
                        if SYMBOL_RE.match(sym):
                            stocks.add(sym)
                    break
        except Exception as e:
            print(f"⚠️ Wikipedia ({url}): {e}")
    return stocks


def fetch_finnhub_symbols():
    print("📥 Récupération annuaire US via Finnhub...")
    url = f"https://finnhub.io/api/v1/stock/symbol?exchange=US&token={FINNHUB_API_KEY}"
    try:
        r = requests.get(url, timeout=20)
        if r.status_code != 200:
            print(f"Erreur Finnhub code {r.status_code}")
            return []
        return r.json()
    except Exception as e:
        print(f"Erreur Finnhub: {e}")
        return []


def build_universe():
    """
    Univers déterministe : indices de référence + Finnhub filtré (NYSE/NASDAQ/ARCA).
    Plus d'échantillon aléatoire — BHVN et les titres liquides sont toujours inclus.
    """
    index_stocks = fetch_index_tickers()
    finnhub = fetch_finnhub_symbols()

    stocks = set(index_stocks)
    etfs = set(CORE_ETFS)

    finnhub_stocks = 0
    finnhub_etfs = 0
    skipped_otc = 0

    for item in finnhub:
        symbol = item.get("symbol", "").upper().strip()
        sec_type = item.get("type", "").upper().strip()
        mic = item.get("mic", "").upper().strip()

        if not SYMBOL_RE.match(symbol):
            continue
        if mic:
            if mic not in MAJOR_MICS:
                skipped_otc += 1
                continue
        elif sec_type not in STOCK_TYPES and sec_type not in ETF_TYPES:
            continue

        if sec_type in ETF_TYPES or mic == "ARCX":
            etfs.add(symbol)
            finnhub_etfs += 1
        elif sec_type in STOCK_TYPES or mic in {"XNYS", "XNAS", "BATS", "XASE"}:
            stocks.add(symbol)
            finnhub_stocks += 1

    # Les ETF ne doivent pas être analysés comme actions
    stocks -= etfs

    stock_list = sorted(stocks)
    etf_list = sorted(etfs)

    print(
        f"🌍 Univers : {len(stock_list)} actions "
        f"(indices: {len(index_stocks)}, Finnhub: +{finnhub_stocks}) | "
        f"{len(etf_list)} ETF | {skipped_otc} symboles OTC ignorés"
    )
    return stock_list, etf_list


def download_chunked(tickers, period="3mo"):
    """Téléchargement par lots — plus fiable que 800 tickers d'un coup."""
    if not tickers:
        return pd.DataFrame(), pd.DataFrame(), 0, 0

    yahoo_tickers = [to_yahoo_symbol(t) for t in tickers]
    yahoo_to_orig = {to_yahoo_symbol(t): t for t in tickers}

    all_closes = {}
    all_volumes = {}
    ok, fail = 0, 0

    for i in range(0, len(yahoo_tickers), CHUNK_SIZE):
        chunk = yahoo_tickers[i : i + CHUNK_SIZE]
        chunk_num = i // CHUNK_SIZE + 1
        total_chunks = (len(yahoo_tickers) + CHUNK_SIZE - 1) // CHUNK_SIZE
        print(f"   Lot {chunk_num}/{total_chunks} ({len(chunk)} tickers)...")

        try:
            data = yf.download(
                chunk,
                period=period,
                progress=False,
                ignore_tz=True,
                threads=False,
                auto_adjust=True,
            )
        except Exception:
            fail += len(chunk)
            time.sleep(CHUNK_DELAY_SEC)
            continue

        if data is None or data.empty or "Close" not in data:
            fail += len(chunk)
            time.sleep(CHUNK_DELAY_SEC)
            continue

        closes = data["Close"]
        volumes = data["Volume"]

        if isinstance(closes, pd.Series):
            orig = yahoo_to_orig.get(chunk[0], chunk[0])
            if closes.dropna().shape[0] >= 2:
                all_closes[orig] = closes
                all_volumes[orig] = volumes
                ok += 1
            else:
                fail += 1
        else:
            for yt in chunk:
                orig = yahoo_to_orig.get(yt, yt)
                if yt not in closes.columns:
                    fail += 1
                    continue
                c = closes[yt].dropna()
                v = volumes[yt].dropna()
                if len(c) >= 2 and len(v) >= 2:
                    all_closes[orig] = closes[yt]
                    all_volumes[orig] = volumes[yt]
                    ok += 1
                else:
                    fail += 1

        time.sleep(CHUNK_DELAY_SEC)

    return all_closes, all_volumes, ok, fail


def pct_return(series, days):
    if len(series) < days + 1:
        return None
    base = float(series.iloc[-days - 1])
    if base == 0:
        return None
    return (float(series.iloc[-1]) / base - 1) * 100


def momentum_score(var_1d, var_5d, var_20d, rs_20d, vol_ratio, above_sma20):
    score = 0.0
    if var_1d is not None and var_1d > 0:
        score += min(var_1d * 2, 20)
    if var_5d is not None and var_5d > 0:
        score += min(var_5d, 25)
    if var_20d is not None and var_20d > 0:
        score += min(var_20d * 0.8, 30)
    if rs_20d is not None and rs_20d > 0:
        score += min(rs_20d * 0.6, 20)
    if vol_ratio is not None and vol_ratio > 1:
        score += min((vol_ratio - 1) * 5, 10)
    if above_sma20:
        score += 5
    return round(min(score, 100), 1)


def get_spy_benchmark():
    closes, _, ok, _ = download_chunked(["SPY"], period="3mo")
    if ok == 0 or "SPY" not in closes:
        return None
    return pct_return(closes["SPY"], 20)


def analyze_group(tickers, is_etf=False, spy_ret_20d=None):
    if not tickers:
        return pd.DataFrame()

    label = "ETF" if is_etf else "Actions"
    print(f"📡 Analyse {label} : {len(tickers)} tickers (lots de {CHUNK_SIZE})...")

    all_closes, all_volumes, ok, fail = download_chunked(tickers, period="3mo")
    print(f"   ✅ {ok} récupérés | ❌ {fail} échecs")

    results = []
    for ticker, c in all_closes.items():
        try:
            v = all_volumes.get(ticker)
            if v is None:
                continue

            c = c.dropna()
            v = v.dropna()
            if len(c) < 22 or len(v) < 6:
                continue

            price = float(c.iloc[-1])
            var_1d = pct_return(c, 1)
            var_5d = pct_return(c, 5)
            var_20d = pct_return(c, 20)

            prior_vol = v.iloc[:-1]
            avg_vol = float(prior_vol.tail(20).mean()) if len(prior_vol) >= 5 else float(prior_vol.mean())
            today_vol = float(v.iloc[-1])
            vol_ratio = today_vol / avg_vol if avg_vol > 0 else 0

            sma20 = float(c.tail(20).mean())
            above_sma20 = price > sma20

            rs_20d = (var_20d - spy_ret_20d) if (var_20d is not None and spy_ret_20d is not None) else None
            score = momentum_score(var_1d, var_5d, var_20d, rs_20d, vol_ratio, above_sma20)

            if is_etf:
                passes = (
                    score >= 40
                    and var_20d is not None
                    and var_20d >= 3
                    and var_1d is not None
                    and var_1d >= 1.5
                )
            else:
                passes = (
                    score >= 50
                    and price >= 2.0
                    and var_20d is not None
                    and var_20d >= 5
                    and (rs_20d is None or rs_20d >= 0)
                )

            if passes:
                results.append(
                    {
                        "Ticker": ticker,
                        "Score": score,
                        "Prix": price,
                        "Var1j": var_1d or 0,
                        "Var5j": var_5d or 0,
                        "Var20j": var_20d or 0,
                        "RS20j": rs_20d or 0,
                        "VolRatio": vol_ratio,
                    }
                )
        except Exception:
            continue

    df = pd.DataFrame(results)
    if not df.empty:
        df = df.sort_values(by=["Score", "Var20j"], ascending=False)
    return df


def short_expiry(exp_str):
    try:
        return datetime.strptime(exp_str, "%Y-%m-%d").strftime("%b-%y")
    except ValueError:
        return exp_str


def _uoa_from_chain(df, side, expiry, min_volume):
    """Extrait les contrats avec volume > OI et Vol/OI >= seuil."""
    if df is None or df.empty:
        return []

    hits = []
    for row in df.itertuples(index=False):
        vol = getattr(row, "volume", None)
        oi = getattr(row, "openInterest", None)
        strike = getattr(row, "strike", None)
        if vol is None or oi is None or strike is None:
            continue
        if pd.isna(vol) or pd.isna(oi) or pd.isna(strike):
            continue

        vol = int(vol)
        oi = int(oi)
        if vol < min_volume or oi <= 0 or vol <= oi:
            continue

        vol_oi = vol / oi
        if vol_oi < OPTIONS_MIN_VOL_OI:
            continue

        hits.append(
            {
                "side": side,
                "strike": float(strike),
                "expiry": expiry,
                "volume": vol,
                "oi": oi,
                "vol_oi": round(vol_oi, 1),
            }
        )
    return hits


def scan_ticker_options(ticker, is_etf=False):
    """Scan UOA (Vol > OI) sur 2 échéances proches — 1 ticker à la fois."""
    try:
        tk = yf.Ticker(to_yahoo_symbol(ticker))
        expirations = tk.options
        if not expirations:
            return []
    except Exception:
        return []

    min_volume = OPTIONS_MIN_VOLUME_ETF if is_etf else OPTIONS_MIN_VOLUME_STOCK
    hits = []

    for exp in expirations[:OPTIONS_MAX_EXPIRATIONS]:
        try:
            chain = tk.option_chain(exp)
            hits.extend(_uoa_from_chain(chain.calls, "CALL", exp, min_volume))
            hits.extend(_uoa_from_chain(chain.puts, "PUT", exp, min_volume))
        except Exception:
            pass
        time.sleep(OPTIONS_DELAY_SEC)

    hits.sort(key=lambda x: x["vol_oi"], reverse=True)
    return hits[:1]


def scan_unusual_options(df_stocks, df_etfs):
    """
    Deuxième passe options sur les finalistes momentum seulement.
    ~15 tickers × 2 échéances ≈ 30 requêtes Yahoo.
    """
    candidates = []
    if not df_stocks.empty:
        for _, row in df_stocks.head(10).iterrows():
            candidates.append((row["Ticker"], False))
    if not df_etfs.empty:
        for _, row in df_etfs.head(5).iterrows():
            candidates.append((row["Ticker"], True))

    if not candidates:
        return {}

    print(f"🐳 Scan options UOA sur {len(candidates)} finalistes...")
    options_map = {}
    found = 0

    for ticker, is_etf in candidates:
        contracts = scan_ticker_options(ticker, is_etf=is_etf)
        if contracts:
            options_map[ticker] = contracts
            found += 1
            top = contracts[0]
            print(
                f"   {ticker}: {top['side']} {top['strike']} {short_expiry(top['expiry'])} "
                f"Vol/OI {top['vol_oi']:.1f}x"
            )

    print(f"   🐳 UOA détectée sur {found}/{len(candidates)} tickers")
    return options_map


def format_options_line(ticker, options_map):
    contracts = options_map.get(ticker)
    if not contracts:
        return ""
    c = contracts[0]
    return (
        f"   🐳 {c['side']} {c['strike']:.1f} {short_expiry(c['expiry'])} | "
        f"Vol:{c['volume']:,} > OI:{c['oi']:,} | {c['vol_oi']:.1f}x\n"
    )


def format_telegram(df_stocks, df_etfs, spy_ret_20d, options_map=None):
    spy_line = f"SPY 20j: {spy_ret_20d:+.1f}%" if spy_ret_20d is not None else ""
    message = f"🚀 *AnisTrade — MOMENTUM + OPTIONS*\n_{spy_line}_\n\n"
    options_map = options_map or {}

    if not df_stocks.empty:
        message += "📈 *ACTIONS*\n"
        for _, row in df_stocks.head(10).iterrows():
            message += (
                f"🔥 *{row['Ticker']}* Score:{row['Score']:.0f} | "
                f"+{row['Var1j']:.1f}% | 5j:{row['Var5j']:+.1f}% | 20j:{row['Var20j']:+.1f}% | "
                f"RS:{row['RS20j']:+.1f}% | Vol:{row['VolRatio']:.1f}x | {row['Prix']:.2f}$\n"
            )
            message += format_options_line(row["Ticker"], options_map)
        message += "\n"

    if not df_etfs.empty:
        message += "📊 *ETF*\n"
        for _, row in df_etfs.head(5).iterrows():
            message += (
                f"⚡ *{row['Ticker']}* Score:{row['Score']:.0f} | "
                f"+{row['Var1j']:.1f}% | 5j:{row['Var5j']:+.1f}% | 20j:{row['Var20j']:+.1f}% | "
                f"Vol:{row['VolRatio']:.1f}x | {row['Prix']:.2f}$\n"
            )
            message += format_options_line(row["Ticker"], options_map)
        message += "\n"

    uoa_count = len(options_map)
    if uoa_count:
        message += f"🐳 _{uoa_count} ticker(s) avec activité options inhabituelle (Vol > OI, ratio ≥ {OPTIONS_MIN_VOL_OI}x)_\n"
    message += "⚠️ _Vérifiez catalyseurs et contexte avant trade._"
    return message


def main():
    if not all([FINNHUB_API_KEY, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID]):
        raise Exception("Clés d'API ou Telegram manquantes. Vérifiez vos Secrets GitHub.")

    stocks, etfs = build_universe()

    spy_ret_20d = get_spy_benchmark()
    if spy_ret_20d is not None:
        print(f"📊 Benchmark SPY 20j : {spy_ret_20d:+.2f}%")

    df_stocks = analyze_group(stocks, is_etf=False, spy_ret_20d=spy_ret_20d)
    df_etfs = analyze_group(etfs, is_etf=True, spy_ret_20d=spy_ret_20d)

    # Vérification diagnostic : BHVN toujours dans l'univers
    if "BHVN" in stocks:
        in_results = "BHVN" in df_stocks["Ticker"].values if not df_stocks.empty else False
        print(f"🔍 BHVN dans univers: oui | signal: {'oui' if in_results else 'non (pas de momentum actuel)'}")

    if not df_stocks.empty or not df_etfs.empty:
        options_map = scan_unusual_options(df_stocks, df_etfs)
        message = format_telegram(df_stocks, df_etfs, spy_ret_20d, options_map)
        send_telegram(message)
        print("🚀 Alerte envoyée avec succès !")
    else:
        print("Calme plat. Aucune étoile montante détectée.")


if __name__ == "__main__":
    main()
