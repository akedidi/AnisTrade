import os
import re
import time
import logging
from datetime import datetime
import requests
import warnings
import pandas as pd
import yfinance as yf

warnings.simplefilter(action="ignore", category=FutureWarning)
logging.getLogger("yfinance").setLevel(logging.CRITICAL)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

NASDAQ_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/symdir/nasdaqlisted.txt"
OTHER_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/symdir/otherlisted.txt"
OTHER_EXCHANGES = {"N", "A", "P", "Z"}

SYMBOL_RE = re.compile(r"^[A-Z]{1,5}$")
USER_AGENT = "AnisTradeBot/4.0 (rising-stars)"

US_EXCHANGES = {"NMS", "NYQ", "NGM", "NCM", "PCX", "BTS", "ASE"}

# Screeners — priorité mid/small caps (pas most_actives en tête)
RUNNER_SCREENERS = [
    "small_cap_gainers",
    "aggressive_small_caps",
    "most_shorted_stocks",
    "day_gainers",
]
ALL_STOCK_SCREENERS = RUNNER_SCREENERS + ["most_actives"]
SCREENER_COUNT = 250
SCREENER_DELAY_SEC = 0.2

# Rising Stars — profil « peut encore doubler »
RUNNER_MIN_PRICE = 2.0
RUNNER_MAX_PRICE = 80.0
RUNNER_MAX_MARKET_CAP = 10_000_000_000
RUNNER_MIN_VAR20 = 15.0
RUNNER_MAX_VAR20 = 60.0
RUNNER_MIN_VOL_RATIO = 1.2
RUNNER_MAX_DOWNLOAD = 120
RUNNER_TOP_ALERTS = 10
EXTENDED_TOP_ALERTS = 5

CHUNK_SIZE = 80
CHUNK_DELAY_SEC = 0.25

# Options — CALL OTM bullish uniquement sur les runners
OPTIONS_MAX_EXPIRATIONS = 2
OPTIONS_MIN_VOL_OI = 2.0
OPTIONS_MIN_OI = 10
OPTIONS_MIN_VOLUME_STOCK = 100
OPTIONS_OTM_MIN_PCT = 1.02
OPTIONS_DELAY_SEC = 0.25
OPTIONS_CONTRACTS_PER_TICKER = 2


def to_yahoo_symbol(symbol: str) -> str:
    return symbol.replace(".", "-")


def log(msg):
    print(msg, flush=True)


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


def fetch_nasdaq_trader_file(url):
    headers = {"User-Agent": USER_AGENT}
    r = requests.get(url, headers=headers, timeout=30)
    r.raise_for_status()
    return r.text


def _parse_nasdaq_row(row, stocks, etfs, stats_key):
    symbol = row.get("Symbol", "").strip().upper()
    if not SYMBOL_RE.match(symbol):
        return
    if row.get("Test Issue", "").strip().upper() == "Y":
        return
    if row.get("ETF", "").strip().upper() == "Y":
        etfs.add(symbol)
        stats_key["etfs"] += 1
    else:
        stocks.add(symbol)
        stats_key["stocks"] += 1


def _parse_other_row(row, stocks, etfs, stats_key):
    symbol = row.get("ACT Symbol", "").strip().upper()
    if not SYMBOL_RE.match(symbol):
        return
    if row.get("Exchange", "").strip().upper() not in OTHER_EXCHANGES:
        stats_key["skipped_exchange"] += 1
        return
    if row.get("Test Issue", "").strip().upper() == "Y":
        return
    if row.get("ETF", "").strip().upper() == "Y":
        etfs.add(symbol)
        stats_key["etfs"] += 1
    else:
        stocks.add(symbol)
        stats_key["stocks"] += 1


def fetch_nasdaq_trader_symbols():
    log("📥 Téléchargement annuaire NASDAQ Trader (2 fichiers)...")
    stocks = set()
    etfs = set()
    stats = {"stocks": 0, "etfs": 0, "skipped_exchange": 0}

    try:
        nasdaq_text = fetch_nasdaq_trader_file(NASDAQ_LISTED_URL)
        for line in nasdaq_text.splitlines():
            if not line or line.startswith("Symbol|") or line.startswith("File Creation"):
                continue
            parts = line.split("|")
            if len(parts) < 8:
                continue
            _parse_nasdaq_row(
                {"Symbol": parts[0], "Test Issue": parts[3], "ETF": parts[6]},
                stocks,
                etfs,
                stats,
            )

        other_text = fetch_nasdaq_trader_file(OTHER_LISTED_URL)
        for line in other_text.splitlines():
            if not line or line.startswith("ACT Symbol|") or line.startswith("File Creation"):
                continue
            parts = line.split("|")
            if len(parts) < 8:
                continue
            _parse_other_row(
                {
                    "ACT Symbol": parts[0],
                    "Exchange": parts[2],
                    "ETF": parts[4],
                    "Test Issue": parts[6],
                },
                stocks,
                etfs,
                stats,
            )
    except Exception as e:
        log(f"⚠️ Erreur NASDAQ Trader: {e}")
        return set(), set()

    log(f"   NASDAQ Trader : {len(stocks)} actions, {len(etfs)} ETF")
    return stocks, etfs


def _quote_context(quote):
    return {
        "change_pct": quote.get("regularMarketChangePercent"),
        "price": quote.get("regularMarketPrice") or quote.get("intradayprice"),
        "volume": quote.get("regularMarketVolume") or quote.get("dayvolume"),
        "market_cap": quote.get("marketCap"),
    }


def _extract_screener_quotes(quotes, quote_type="EQUITY"):
    by_symbol = {}
    for q in quotes or []:
        if q.get("quoteType") != quote_type:
            continue
        if q.get("exchange") not in US_EXCHANGES:
            continue
        sym = q.get("symbol", "").upper().strip()
        if not SYMBOL_RE.match(sym):
            continue
        by_symbol[sym] = _quote_context(q)
    return by_symbol


def fetch_screener_universe():
    log("📡 Screeners Yahoo (rising stars)...")
    stock_quotes = {}
    sources = {
        "small_cap": set(),
        "aggressive": set(),
        "shorted": set(),
        "gainers": set(),
        "actives": set(),
    }

    for name in ALL_STOCK_SCREENERS:
        try:
            resp = yf.screen(name, count=SCREENER_COUNT)
            quotes = resp.get("quotes", []) if isinstance(resp, dict) else []
            found = _extract_screener_quotes(quotes, "EQUITY")
            log(f"   {name}: {len(found)} actions")
            stock_quotes.update(found)
            key = {
                "small_cap_gainers": "small_cap",
                "aggressive_small_caps": "aggressive",
                "most_shorted_stocks": "shorted",
                "day_gainers": "gainers",
                "most_actives": "actives",
            }.get(name)
            if key:
                sources[key] |= set(found.keys())
        except Exception as e:
            log(f"   ⚠️ {name}: {e}")
        time.sleep(SCREENER_DELAY_SEC)

    log(f"   Total screener : {len(stock_quotes)} actions uniques")
    return stock_quotes, sources


def _screener_priority(sym, sources):
    score = 0
    if sym in sources["shorted"]:
        score += 4
    if sym in sources["small_cap"]:
        score += 3
    if sym in sources["aggressive"]:
        score += 3
    if sym in sources["gainers"]:
        score += 1
    if sym in sources["actives"]:
        score += 0
    return score


def _passes_screener_prefilter(ctx, sym, sources):
    price = ctx.get("price")
    cap = ctx.get("market_cap")
    if price is None or price < RUNNER_MIN_PRICE or price > RUNNER_MAX_PRICE:
        return False
    if cap is not None and cap > RUNNER_MAX_MARKET_CAP:
        return False
    if _screener_priority(sym, sources) == 0 and sym not in sources["actives"]:
        return False
    return True


def build_runner_candidates(stock_quotes, nasdaq_stocks, sources):
    """Mid/small caps des screeners — pas les mega-caps most_actives."""
    candidates = []
    for sym, ctx in stock_quotes.items():
        if sym not in nasdaq_stocks:
            continue
        if not _passes_screener_prefilter(ctx, sym, sources):
            continue
        priority = _screener_priority(sym, sources)
        vol = ctx.get("volume") or 0
        candidates.append((sym, priority, vol, ctx))

    candidates.sort(key=lambda x: (x[1], x[2]), reverse=True)
    selected = [sym for sym, _, _, _ in candidates[:RUNNER_MAX_DOWNLOAD]]
    log(
        f"🎯 Cibles Rising Stars : {len(selected)} actions "
        f"(prix {RUNNER_MIN_PRICE}-{RUNNER_MAX_PRICE}$, cap <{RUNNER_MAX_MARKET_CAP // 1_000_000_000}Md$)"
    )
    return selected, {sym: ctx for sym, _, _, ctx in candidates}


def download_chunked(tickers, period="3mo"):
    if not tickers:
        return {}, {}, 0, 0

    yahoo_tickers = [to_yahoo_symbol(t) for t in tickers]
    yahoo_to_orig = {to_yahoo_symbol(t): t for t in tickers}
    all_closes, all_volumes = {}, {}
    ok, fail = 0, 0

    for i in range(0, len(yahoo_tickers), CHUNK_SIZE):
        chunk = yahoo_tickers[i : i + CHUNK_SIZE]
        chunk_num = i // CHUNK_SIZE + 1
        total = (len(yahoo_tickers) + CHUNK_SIZE - 1) // CHUNK_SIZE
        log(f"   Lot {chunk_num}/{total} ({len(chunk)} tickers)...")

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

        closes, volumes = data["Close"], data["Volume"]
        if isinstance(closes, pd.Series):
            orig = yahoo_to_orig.get(chunk[0], chunk[0])
            if closes.dropna().shape[0] >= 22:
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
                if len(c) >= 22:
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


def runner_score(var_1d, var_5d, var_20d, rs_20d, vol_ratio, is_shorted, accelerating):
    score = 0.0
    if var_5d is not None and var_5d > 0:
        score += min(var_5d * 1.2, 25)
    if var_20d is not None and RUNNER_MIN_VAR20 <= var_20d <= RUNNER_MAX_VAR20:
        score += min(var_20d * 0.9, 35)
    if var_1d is not None and var_1d > 0:
        score += min(var_1d * 1.5, 15)
    if rs_20d is not None and rs_20d > 0:
        score += min(rs_20d * 0.5, 15)
    if vol_ratio is not None and vol_ratio >= RUNNER_MIN_VOL_RATIO:
        score += min((vol_ratio - 1) * 8, 15)
    if is_shorted:
        score += 8
    if accelerating:
        score += 10
    return round(min(score, 100), 1)


def get_spy_benchmark():
    closes, _, ok, _ = download_chunked(["SPY"], period="3mo")
    if ok == 0 or "SPY" not in closes:
        return None
    return pct_return(closes["SPY"], 20)


def analyze_runners(tickers, sources, spy_ret_20d):
    log(f"📡 Analyse momentum {len(tickers)} tickers...")
    all_closes, all_volumes, ok, fail = download_chunked(tickers, period="3mo")
    log(f"   ✅ {ok} récupérés | ❌ {fail} échecs")

    runners, extended = [], []
    for ticker in tickers:
        c = all_closes.get(ticker)
        v = all_volumes.get(ticker)
        if c is None or v is None:
            continue

        c, v = c.dropna(), v.dropna()
        if len(c) < 22 or len(v) < 6:
            continue

        price = float(c.iloc[-1])
        var_1d = pct_return(c, 1)
        var_5d = pct_return(c, 5)
        var_20d = pct_return(c, 20)

        prior_vol = v.iloc[:-1]
        avg_vol = float(prior_vol.tail(20).mean()) if len(prior_vol) >= 5 else float(prior_vol.mean())
        vol_ratio = float(v.iloc[-1]) / avg_vol if avg_vol > 0 else 0

        rs_20d = (var_20d - spy_ret_20d) if (var_20d is not None and spy_ret_20d is not None) else None
        is_shorted = ticker in sources["shorted"]
        accelerating = (
            var_5d is not None
            and var_20d is not None
            and var_5d > 0
            and var_5d > (var_20d / 4)
        )

        row = {
            "Ticker": ticker,
            "Prix": price,
            "Var1j": var_1d or 0,
            "Var5j": var_5d or 0,
            "Var20j": var_20d or 0,
            "RS20j": rs_20d or 0,
            "VolRatio": vol_ratio,
            "Shorted": is_shorted,
        }

        if price < RUNNER_MIN_PRICE or price > RUNNER_MAX_PRICE:
            continue

        if var_20d is not None and var_20d > RUNNER_MAX_VAR20:
            row["Score"] = runner_score(var_1d, var_5d, var_20d, rs_20d, vol_ratio, is_shorted, accelerating)
            extended.append(row)
            continue

        is_runner = (
            var_20d is not None
            and RUNNER_MIN_VAR20 <= var_20d <= RUNNER_MAX_VAR20
            and vol_ratio >= RUNNER_MIN_VOL_RATIO
            and (rs_20d is None or rs_20d >= 0)
        )
        if is_runner:
            row["Score"] = runner_score(var_1d, var_5d, var_20d, rs_20d, vol_ratio, is_shorted, accelerating)
            runners.append(row)

    df_runners = pd.DataFrame(runners)
    df_extended = pd.DataFrame(extended)
    if not df_runners.empty:
        df_runners = df_runners.sort_values(by=["Score", "Var20j"], ascending=False)
    if not df_extended.empty:
        df_extended = df_extended.sort_values(by="Var20j", ascending=False)
    log(f"   🌟 {len(df_runners)} runners | 📈 {len(df_extended)} déjà étirés (> {RUNNER_MAX_VAR20}% 20j)")
    return df_runners, df_extended


def short_expiry(exp_str):
    try:
        return datetime.strptime(exp_str, "%Y-%m-%d").strftime("%b-%y")
    except ValueError:
        return exp_str


def _uoa_calls_otm(df, expiry, spot_price, min_volume):
    """CALL OTM uniquement — pari haussier directionnel."""
    if df is None or df.empty or spot_price is None:
        return []

    min_strike = spot_price * OPTIONS_OTM_MIN_PCT
    hits = []
    for row in df.itertuples(index=False):
        vol = getattr(row, "volume", None)
        oi = getattr(row, "openInterest", None)
        strike = getattr(row, "strike", None)
        if vol is None or oi is None or strike is None:
            continue
        if pd.isna(vol) or pd.isna(oi) or pd.isna(strike):
            continue

        strike = float(strike)
        if strike < min_strike:
            continue

        vol, oi = int(vol), int(oi)
        if vol < min_volume or oi < OPTIONS_MIN_OI or vol <= oi:
            continue
        vol_oi = vol / oi
        if vol_oi < OPTIONS_MIN_VOL_OI:
            continue

        otm_pct = (strike / spot_price - 1) * 100
        hits.append(
            {
                "side": "CALL",
                "strike": strike,
                "expiry": expiry,
                "volume": vol,
                "oi": oi,
                "vol_oi": round(vol_oi, 1),
                "otm_pct": round(otm_pct, 1),
            }
        )
    return hits


def scan_runner_options(ticker, spot_price):
    try:
        tk = yf.Ticker(to_yahoo_symbol(ticker))
        expirations = tk.options
        if not expirations:
            return []
    except Exception:
        return []

    hits = []
    for exp in expirations[:OPTIONS_MAX_EXPIRATIONS]:
        try:
            chain = tk.option_chain(exp)
            hits.extend(_uoa_calls_otm(chain.calls, exp, spot_price, OPTIONS_MIN_VOLUME_STOCK))
        except Exception:
            pass
        time.sleep(OPTIONS_DELAY_SEC)

    hits.sort(key=lambda x: x["vol_oi"], reverse=True)
    return hits[:OPTIONS_CONTRACTS_PER_TICKER]


def scan_runners_uoa(df_runners):
    if df_runners.empty:
        return {}

    log(f"🐳 Scan CALL OTM sur {min(len(df_runners), RUNNER_TOP_ALERTS)} runners...")
    options_map = {}
    for _, row in df_runners.head(RUNNER_TOP_ALERTS).iterrows():
        ticker = row["Ticker"]
        contracts = scan_runner_options(ticker, row["Prix"])
        if contracts:
            options_map[ticker] = contracts
            top = contracts[0]
            log(
                f"   {ticker}: CALL {top['strike']} OTM +{top['otm_pct']}% "
                f"Vol/OI {top['vol_oi']:.1f}x"
            )
    log(f"   🐳 UOA bullish : {len(options_map)}/{min(len(df_runners), RUNNER_TOP_ALERTS)} runners")
    return options_map


def _format_runner_line(row, options_map):
    line = (
        f"*{row['Ticker']}* Score:{row['Score']:.0f} | "
        f"{row['Var1j']:+.1f}% | 5j:{row['Var5j']:+.1f}% | 20j:{row['Var20j']:+.1f}% | "
        f"Vol:{row['VolRatio']:.1f}x | {row['Prix']:.2f}$"
    )
    if row.get("Shorted"):
        line += " | _short_"
    line += "\n"

    contracts = options_map.get(row["Ticker"])
    if contracts:
        c = contracts[0]
        line += (
            f"   🐳 CALL {c['strike']:.1f} OTM +{c['otm_pct']:.0f}% {short_expiry(c['expiry'])} | "
            f"Vol:{c['volume']:,} > OI:{c['oi']:,} | {c['vol_oi']:.1f}x\n"
        )
    return line


def format_rising_stars_telegram(df_runners, df_extended, spy_ret_20d, options_map):
    spy_line = f"SPY 20j: {spy_ret_20d:+.1f}%" if spy_ret_20d is not None else ""
    message = (
        f"🚀 *AnisTrade — RISING STARS*\n"
        f"_{spy_line} | Prix {RUNNER_MIN_PRICE:.0f}-{RUNNER_MAX_PRICE:.0f}$ | "
        f"Cap <{RUNNER_MAX_MARKET_CAP // 1_000_000_000}Md$ | "
        f"20j: {RUNNER_MIN_VAR20:.0f}-{RUNNER_MAX_VAR20:.0f}% | Vol≥{RUNNER_MIN_VOL_RATIO}x_\n\n"
    )

    if not df_runners.empty:
        message += "🌟 *RUNNERS POTENTIELS* _(marge de doubler)_\n"
        for _, row in df_runners.head(RUNNER_TOP_ALERTS).iterrows():
            message += _format_runner_line(row, options_map)
        message += "\n"
    else:
        message += "🌟 _Aucun runner ne passe les filtres aujourd'hui._\n\n"

    if not df_extended.empty:
        message += f"📈 *DÉJÀ EN RUN* _(>{RUNNER_MAX_VAR20:.0f}% sur 20j — continuation, pas ×2)_\n"
        for _, row in df_extended.head(EXTENDED_TOP_ALERTS).iterrows():
            message += (
                f"_{row['Ticker']}_ {row['Var20j']:+.0f}% 20j | {row['Prix']:.0f}$ "
                f"| Vol:{row['VolRatio']:.1f}x\n"
            )
        message += "\n"

    uoa_n = len(options_map)
    if uoa_n:
        message += f"🐳 _{uoa_n} runner(s) avec CALL OTM inhabituel (Vol > OI, ratio ≥ {OPTIONS_MIN_VOL_OI}x)_\n"
    message += "⚠️ _Pas de garantie +100%. Vérifiez catalyseurs (earnings, FDA, short squeeze)._"
    return message


def main():
    if not all([TELEGRAM_TOKEN, TELEGRAM_CHAT_ID]):
        raise Exception("Secrets Telegram manquants. Vérifiez vos Secrets GitHub.")

    nasdaq_stocks, _ = fetch_nasdaq_trader_symbols()
    stock_quotes, sources = fetch_screener_universe()
    tickers, _ = build_runner_candidates(stock_quotes, nasdaq_stocks, sources)

    if not tickers:
        log("Aucun candidat screener. Fin.")
        return

    spy_ret_20d = get_spy_benchmark()
    if spy_ret_20d is not None:
        log(f"📊 Benchmark SPY 20j : {spy_ret_20d:+.2f}%")

    df_runners, df_extended = analyze_runners(tickers, sources, spy_ret_20d)
    options_map = scan_runners_uoa(df_runners)

    if not df_runners.empty or not df_extended.empty:
        message = format_rising_stars_telegram(df_runners, df_extended, spy_ret_20d, options_map)
        send_telegram(message)
        log("🚀 Alerte Rising Stars envoyée !")
    else:
        log("Calme plat. Aucune étoile montante détectée.")


if __name__ == "__main__":
    main()
