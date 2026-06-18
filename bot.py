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
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY")

NASDAQ_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/symdir/nasdaqlisted.txt"
OTHER_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/symdir/otherlisted.txt"
OTHER_EXCHANGES = {"N", "A", "P", "Z"}
FINNHUB_BASE = "https://finnhub.io/api/v1"

SYMBOL_RE = re.compile(r"^[A-Z]{1,5}$")
USER_AGENT = "AnisTradeBot/5.0 (rising-stars)"

US_EXCHANGES = {"NMS", "NYQ", "NGM", "NCM", "PCX", "BTS", "ASE"}

RUNNER_SCREENERS = [
    "small_cap_gainers",
    "aggressive_small_caps",
    "most_shorted_stocks",
    "day_gainers",
]
ALL_STOCK_SCREENERS = RUNNER_SCREENERS + ["most_actives"]
SCREENER_COUNT = 250
SCREENER_DELAY_SEC = 0.2

RUNNER_MIN_PRICE = 2.0
RUNNER_MAX_PRICE = 80.0
RUNNER_MIN_MARKET_CAP = 50_000_000
RUNNER_MAX_MARKET_CAP = 10_000_000_000
RUNNER_MIN_VAR20 = 15.0
RUNNER_MAX_VAR20 = 60.0
RUNNER_STRICT_VOL_RATIO = 1.5
ANALYST_TARGET_MIN_UPSIDE = 30.0
ANALYST_BUY_BONUS = 10
ANALYST_UPSIDE_BONUS_CAP = 12
FINNHUB_NEWS_BONUS = 8
FINNHUB_REC_BONUS = 8
BUY_RATINGS = {"buy", "strong_buy", "strongbuy", "outperform", "overweight"}
RUNNER_MAX_DOWNLOAD = 120
RUNNER_TOP_ALERTS = 15
STEALTH_TOP_ALERTS = 10
EXTENDED_TOP_ALERTS = 5

STEALTH_MAX_VOL_RATIO = 1.5
STEALTH_MIN_OPT_VOL_OI = 5.0

CHUNK_SIZE = 80
CHUNK_DELAY_SEC = 0.25
FINNHUB_DELAY_SEC = 0.15

SECTOR_ORDER = ["Biotech", "IA", "Industrie", "Retail", "Autre"]
SECTOR_LABELS = {
    "Biotech": "🧬 BIOTECH",
    "IA": "🤖 IA / TECH",
    "Industrie": "🏭 INDUSTRIE",
    "Retail": "🛍 RETAIL",
    "Autre": "📦 AUTRE",
}

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


def classify_sector(sector, industry):
    s = (sector or "").lower()
    i = (industry or "").lower()
    bio_kw = ("biotech", "biotechnology", "drug", "pharma", "therapeutic", "genomic")
    ai_kw = ("software", "semiconductor", "internet", "cloud", "artificial", "computer", "ai ")
    retail_kw = ("retail", "apparel", "department", "specialty", "e-commerce", "ecommerce")

    if any(k in i for k in bio_kw) or (s == "healthcare" and "biotech" in i):
        return "Biotech"
    if s == "healthcare":
        return "Biotech"
    if s == "technology" or any(k in i for k in ai_kw):
        return "IA"
    if s == "industrials":
        return "Industrie"
    if s in ("consumer cyclical", "consumer defensive") or any(k in i for k in retail_kw):
        return "Retail"
    return "Autre"


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
    stocks, etfs = set(), set()
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
                stocks, etfs, stats,
            )
        other_text = fetch_nasdaq_trader_file(OTHER_LISTED_URL)
        for line in other_text.splitlines():
            if not line or line.startswith("ACT Symbol|") or line.startswith("File Creation"):
                continue
            parts = line.split("|")
            if len(parts) < 8:
                continue
            _parse_other_row(
                {"ACT Symbol": parts[0], "Exchange": parts[2], "ETF": parts[4], "Test Issue": parts[6]},
                stocks, etfs, stats,
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
        if SYMBOL_RE.match(sym):
            by_symbol[sym] = _quote_context(q)
    return by_symbol


def fetch_screener_universe():
    log("📡 Screeners Yahoo (rising stars)...")
    stock_quotes = {}
    sources = {"small_cap": set(), "aggressive": set(), "shorted": set(), "gainers": set(), "actives": set()}
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
    return score


def _passes_screener_prefilter(ctx, sym, sources):
    price, cap = ctx.get("price"), ctx.get("market_cap")
    if price is None or price < RUNNER_MIN_PRICE or price > RUNNER_MAX_PRICE:
        return False
    if cap is not None and (cap < RUNNER_MIN_MARKET_CAP or cap > RUNNER_MAX_MARKET_CAP):
        return False
    if _screener_priority(sym, sources) == 0 and sym not in sources["actives"]:
        return False
    return True


def build_runner_candidates(stock_quotes, nasdaq_stocks, sources):
    candidates = []
    for sym, ctx in stock_quotes.items():
        if sym not in nasdaq_stocks or not _passes_screener_prefilter(ctx, sym, sources):
            continue
        candidates.append((sym, _screener_priority(sym, sources), ctx.get("volume") or 0, ctx))
    candidates.sort(key=lambda x: (x[1], x[2]), reverse=True)
    selected = [sym for sym, _, _, _ in candidates[:RUNNER_MAX_DOWNLOAD]]
    log(
        f"🎯 Cibles Rising Stars : {len(selected)} actions "
        f"(cap ≥{RUNNER_MIN_MARKET_CAP // 1_000_000}M$, prix {RUNNER_MIN_PRICE}-{RUNNER_MAX_PRICE}$)"
    )
    return selected


def download_chunked(tickers, period="3mo"):
    if not tickers:
        return {}, {}, 0, 0
    yahoo_tickers = [to_yahoo_symbol(t) for t in tickers]
    yahoo_to_orig = {to_yahoo_symbol(t): t for t in tickers}
    all_closes, all_volumes = {}, {}
    ok, fail = 0, 0
    for i in range(0, len(yahoo_tickers), CHUNK_SIZE):
        chunk = yahoo_tickers[i : i + CHUNK_SIZE]
        log(f"   Lot {i // CHUNK_SIZE + 1}/{(len(yahoo_tickers) + CHUNK_SIZE - 1) // CHUNK_SIZE} ({len(chunk)} tickers)...")
        try:
            data = yf.download(chunk, period=period, progress=False, ignore_tz=True, threads=False, auto_adjust=True)
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
                all_closes[orig], all_volumes[orig] = closes, volumes
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
                    all_closes[orig], all_volumes[orig] = closes[yt], volumes[yt]
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


def fetch_analyst_meta(tickers):
    log(f"📊 Métadonnées Yahoo ({len(tickers)} tickers)...")
    meta = {}
    for i, ticker in enumerate(tickers, 1):
        entry = {
            "recommendation": "", "is_buy": False, "target": None,
            "upside_pct": None, "market_cap": None, "sector": "", "industry": "", "category": "Autre",
        }
        try:
            info = yf.Ticker(to_yahoo_symbol(ticker)).info
            rec = (info.get("recommendationKey") or "").lower()
            target = info.get("targetMeanPrice") or info.get("targetMedianPrice")
            price = info.get("currentPrice") or info.get("regularMarketPrice")
            sector, industry = info.get("sector") or "", info.get("industry") or ""
            entry.update({
                "recommendation": rec,
                "is_buy": rec in BUY_RATINGS,
                "market_cap": info.get("marketCap"),
                "sector": sector,
                "industry": industry,
                "category": classify_sector(sector, industry),
            })
            if target and price and float(price) > 0:
                entry["target"] = float(target)
                entry["upside_pct"] = (float(target) / float(price) - 1) * 100
        except Exception:
            pass
        meta[ticker] = entry
        if i % 25 == 0:
            time.sleep(0.3)
    log(f"   {sum(1 for m in meta.values() if m['is_buy'])} Buy (Yahoo)")
    return meta


def fetch_finnhub_meta(tickers):
    """Finnhub — uniquement sur les finalistes (recommendation + news sentiment)."""
    empty = {
        "rec_buy_pct": None, "news_score": None,
        "bullish_pct": None, "bearish_pct": None, "finnhub_ok": False,
    }
    if not FINNHUB_API_KEY:
        log("📊 Finnhub : clé absente — sentiment ignoré")
        return {t: dict(empty) for t in tickers}

    log(f"📊 Finnhub sentiment ({len(tickers)} finalistes)...")
    meta = {}
    for i, ticker in enumerate(tickers, 1):
        entry = dict(empty)
        params = {"symbol": ticker, "token": FINNHUB_API_KEY}
        try:
            rec_resp = requests.get(f"{FINNHUB_BASE}/stock/recommendation", params=params, timeout=10)
            if rec_resp.ok:
                rec_data = rec_resp.json()
                if isinstance(rec_data, list) and rec_data:
                    latest = rec_data[0]
                    buy = int(latest.get("buy", 0) or 0) + int(latest.get("strongBuy", 0) or 0)
                    total = buy + int(latest.get("hold", 0) or 0) + int(latest.get("sell", 0) or 0) + int(latest.get("strongSell", 0) or 0)
                    if total > 0:
                        entry["rec_buy_pct"] = round(100 * buy / total, 1)
            time.sleep(FINNHUB_DELAY_SEC)
            news_resp = requests.get(f"{FINNHUB_BASE}/news-sentiment", params=params, timeout=10)
            if news_resp.ok:
                news = news_resp.json()
                entry["news_score"] = news.get("companyNewsScore")
                sent = news.get("sentiment") or {}
                entry["bullish_pct"] = sent.get("bullishPercent")
                entry["bearish_pct"] = sent.get("bearishPercent")
            entry["finnhub_ok"] = True
        except Exception as e:
            log(f"   ⚠️ Finnhub {ticker}: {e}")
        meta[ticker] = entry
        if i % 10 == 0:
            time.sleep(0.5)
    ok = sum(1 for m in meta.values() if m["finnhub_ok"])
    log(f"   Finnhub OK : {ok}/{len(tickers)}")
    return meta


def _passes_market_cap(analyst):
    cap = analyst.get("market_cap")
    return cap is not None and RUNNER_MIN_MARKET_CAP <= cap <= RUNNER_MAX_MARKET_CAP


def runner_score(var_1d, var_5d, var_20d, rs_20d, vol_ratio, is_shorted, accelerating, is_buy, target_upside, finnhub):
    score = 0.0
    if var_5d is not None and var_5d > 0:
        score += min(var_5d * 1.2, 25)
    if var_20d is not None and RUNNER_MIN_VAR20 <= var_20d <= RUNNER_MAX_VAR20:
        score += min(var_20d * 0.9, 35)
    if var_1d is not None and var_1d > 0:
        score += min(var_1d * 1.5, 15)
    if rs_20d is not None and rs_20d > 0:
        score += min(rs_20d * 0.5, 15)
    if vol_ratio is not None and vol_ratio >= RUNNER_STRICT_VOL_RATIO:
        score += min((vol_ratio - 1) * 8, 15)
    if is_shorted:
        score += 8
    if accelerating:
        score += 10
    if is_buy:
        score += ANALYST_BUY_BONUS
    if target_upside is not None and target_upside >= ANALYST_TARGET_MIN_UPSIDE:
        score += min(min(target_upside, 80) * 0.12, ANALYST_UPSIDE_BONUS_CAP)
    if finnhub:
        ns = finnhub.get("news_score")
        if ns is not None and ns >= 0.55:
            score += FINNHUB_NEWS_BONUS
        rb = finnhub.get("rec_buy_pct")
        if rb is not None and rb >= 60:
            score += FINNHUB_REC_BONUS
        bp = finnhub.get("bearish_pct")
        if bp is not None and bp >= 0.55:
            score -= 10
    return round(min(max(score, 0), 100), 1)


def get_spy_benchmark():
    closes, _, ok, _ = download_chunked(["SPY"], period="3mo")
    if ok == 0 or "SPY" not in closes:
        return None
    return pct_return(closes["SPY"], 20)


def _build_row(ticker, price, var_1d, var_5d, var_20d, rs_20d, vol_ratio, analyst, sources, finnhub, spy_ret_20d):
    is_shorted = ticker in sources["shorted"]
    accelerating = var_5d is not None and var_20d is not None and var_5d > 0 and var_5d > (var_20d / 4)
    score = runner_score(
        var_1d, var_5d, var_20d, rs_20d, vol_ratio, is_shorted, accelerating,
        analyst.get("is_buy", False), analyst.get("upside_pct"), finnhub,
    )
    return {
        "Ticker": ticker,
        "Prix": price,
        "Var1j": var_1d or 0,
        "Var5j": var_5d or 0,
        "Var20j": var_20d or 0,
        "RS20j": rs_20d or 0,
        "VolRatio": vol_ratio,
        "Shorted": is_shorted,
        "AnalystBuy": analyst.get("is_buy", False),
        "TargetUpside": analyst.get("upside_pct"),
        "Category": analyst.get("category", "Autre"),
        "Score": score,
        "NewsScore": finnhub.get("news_score") if finnhub else None,
        "RecBuyPct": finnhub.get("rec_buy_pct") if finnhub else None,
        "BullishPct": finnhub.get("bullish_pct") if finnhub else None,
    }


def analyze_candidates(tickers, sources, spy_ret_20d):
    log(f"📡 Analyse momentum {len(tickers)} tickers...")
    analyst_meta = fetch_analyst_meta(tickers)
    all_closes, all_volumes, ok, fail = download_chunked(tickers, period="3mo")
    log(f"   ✅ {ok} récupérés | ❌ {fail} échecs")

    momentum_pool, extended = [], []
    for ticker in tickers:
        analyst = analyst_meta.get(ticker, {})
        if not _passes_market_cap(analyst):
            continue
        c, v = all_closes.get(ticker), all_volumes.get(ticker)
        if c is None or v is None:
            continue
        c, v = c.dropna(), v.dropna()
        if len(c) < 22 or len(v) < 6:
            continue

        price = float(c.iloc[-1])
        if price < RUNNER_MIN_PRICE or price > RUNNER_MAX_PRICE:
            continue

        var_1d, var_5d, var_20d = pct_return(c, 1), pct_return(c, 5), pct_return(c, 20)
        prior_vol = v.iloc[:-1]
        avg_vol = float(prior_vol.tail(20).mean()) if len(prior_vol) >= 5 else float(prior_vol.mean())
        vol_ratio = float(v.iloc[-1]) / avg_vol if avg_vol > 0 else 0
        rs_20d = (var_20d - spy_ret_20d) if (var_20d is not None and spy_ret_20d is not None) else None

        if var_20d is not None and var_20d > RUNNER_MAX_VAR20:
            extended.append(_build_row(ticker, price, var_1d, var_5d, var_20d, rs_20d, vol_ratio, analyst, sources, {}, spy_ret_20d))
            continue

        if var_20d is None or var_20d < RUNNER_MIN_VAR20 or (rs_20d is not None and rs_20d < 0):
            continue

        momentum_pool.append(_build_row(ticker, price, var_1d, var_5d, var_20d, rs_20d, vol_ratio, analyst, sources, {}, spy_ret_20d))

    runners, stealth_pool = [], []
    for row in momentum_pool:
        if row["VolRatio"] >= RUNNER_STRICT_VOL_RATIO and row["Var1j"] > 0:
            runners.append(row)
        elif row["VolRatio"] < STEALTH_MAX_VOL_RATIO:
            stealth_pool.append(row)

    df_runners = pd.DataFrame(runners)
    df_stealth_pool = pd.DataFrame(stealth_pool)
    df_extended = pd.DataFrame(extended)
    if not df_runners.empty:
        df_runners = df_runners.sort_values(by=["Score", "Var20j"], ascending=False)
    if not df_stealth_pool.empty:
        df_stealth_pool = df_stealth_pool.sort_values(by=["Score", "Var20j"], ascending=False)
    if not df_extended.empty:
        df_extended = df_extended.sort_values(by="Var20j", ascending=False)

    log(f"   🌟 {len(df_runners)} runners | 🕵️ {len(df_stealth_pool)} pool furtif | 📈 {len(df_extended)} étirés")
    return df_runners, df_stealth_pool, df_extended


def enrich_with_finnhub(df_runners, df_stealth, df_extended):
    tickers = set()
    for df in (df_runners, df_stealth, df_extended):
        if not df.empty:
            tickers.update(df["Ticker"].tolist())
    if not tickers:
        return df_runners, df_stealth, df_extended

    fh = fetch_finnhub_meta(sorted(tickers))

    def _apply(df):
        if df.empty:
            return df
        rows = []
        for _, row in df.iterrows():
            r = row.to_dict()
            meta = fh.get(r["Ticker"], {})
            r["NewsScore"] = meta.get("news_score")
            r["RecBuyPct"] = meta.get("rec_buy_pct")
            r["BullishPct"] = meta.get("bullish_pct")
            if meta.get("finnhub_ok"):
                r["Score"] = runner_score(
                    r["Var1j"], r["Var5j"], r["Var20j"], r["RS20j"], r["VolRatio"],
                    r["Shorted"], r["Var5j"] > 0 and r["Var5j"] > r["Var20j"] / 4,
                    r["AnalystBuy"], r["TargetUpside"], meta,
                )
            rows.append(r)
        out = pd.DataFrame(rows)
        if not out.empty and "Score" in out.columns:
            out = out.sort_values(by=["Score", "Var20j"], ascending=False)
        return out

    return _apply(df_runners), _apply(df_stealth), _apply(df_extended)


def short_expiry(exp_str):
    try:
        return datetime.strptime(exp_str, "%Y-%m-%d").strftime("%b-%y")
    except ValueError:
        return exp_str


def _uoa_calls_otm(df, expiry, spot_price, min_volume):
    if df is None or df.empty or spot_price is None:
        return []
    min_strike = spot_price * OPTIONS_OTM_MIN_PCT
    hits = []
    for row in df.itertuples(index=False):
        vol, oi, strike = getattr(row, "volume", None), getattr(row, "openInterest", None), getattr(row, "strike", None)
        if vol is None or oi is None or strike is None or pd.isna(vol) or pd.isna(oi) or pd.isna(strike):
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
        hits.append({
            "side": "CALL", "strike": strike, "expiry": expiry,
            "volume": vol, "oi": oi, "vol_oi": round(vol_oi, 1),
            "otm_pct": round((strike / spot_price - 1) * 100, 1),
        })
    return hits


def scan_ticker_options(ticker, spot_price):
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


def scan_options_for_df(df, label):
    if df.empty:
        return {}
    log(f"🐳 Options {label} ({len(df)} tickers)...")
    options_map = {}
    for _, row in df.iterrows():
        contracts = scan_ticker_options(row["Ticker"], row["Prix"])
        if contracts:
            options_map[row["Ticker"]] = contracts
            top = contracts[0]
            log(f"   {row['Ticker']}: CALL {top['strike']} Vol/OI {top['vol_oi']:.1f}x")
    return options_map


def build_stealth_df(df_pool, options_map):
    if df_pool.empty:
        return pd.DataFrame()
    rows = []
    for _, row in df_pool.iterrows():
        contracts = options_map.get(row["Ticker"])
        if not contracts:
            continue
        top = contracts[0]
        if top["vol_oi"] >= STEALTH_MIN_OPT_VOL_OI:
            r = row.to_dict()
            r["TopVolOI"] = top["vol_oi"]
            rows.append(r)
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values(by=["TopVolOI", "Score"], ascending=False)
    return df


def _format_sentiment_tags(row):
    tags = []
    if row.get("AnalystBuy"):
        up = row.get("TargetUpside")
        tags.append(f"Buy +{up:.0f}%" if up is not None else "Buy")
    ns = row.get("NewsScore")
    if ns is not None:
        tags.append(f"News:{ns:.2f}")
    rb = row.get("RecBuyPct")
    if rb is not None:
        tags.append(f"FH:{rb:.0f}%Buy")
    return " | _" + ", ".join(tags) + "_" if tags else ""


def _format_stock_line(row, options_map, stealth=False):
    line = (
        f"*{row['Ticker']}* Score:{row['Score']:.0f} | "
        f"{row['Var1j']:+.1f}% | 5j:{row['Var5j']:+.1f}% | 20j:{row['Var20j']:+.1f}% | "
        f"Vol:{row['VolRatio']:.1f}x | {row['Prix']:.2f}$"
    )
    if row.get("Shorted"):
        line += " | _short_"
    line += _format_sentiment_tags(row) + "\n"

    contracts = options_map.get(row["Ticker"])
    if contracts:
        c = contracts[0]
        prefix = "🕵️" if stealth else "🐳"
        warn = " ⚠️jour<0" if stealth and row["Var1j"] < 0 else ""
        line += (
            f"   {prefix} CALL {c['strike']:.1f} OTM +{c['otm_pct']:.0f}% {short_expiry(c['expiry'])} | "
            f"Vol:{c['volume']:,} > OI:{c['oi']:,} | {c['vol_oi']:.1f}x{warn}\n"
        )
    return line


def _format_section_by_sector(df, title, options_map, max_n, stealth=False):
    if df.empty:
        return f"{title}\n_Rien aujourd'hui — gardez votre cash._\n\n"
    message = f"{title}\n"
    subset = df.head(max_n)
    grouped = {cat: [] for cat in SECTOR_ORDER}
    for _, row in subset.iterrows():
        cat = row.get("Category", "Autre")
        grouped.setdefault(cat, []).append(row)
    for cat in SECTOR_ORDER:
        rows = grouped.get(cat, [])
        if not rows:
            continue
        message += f"\n*{SECTOR_LABELS[cat]}*\n"
        for row in rows:
            message += _format_stock_line(row, options_map, stealth=stealth)
    return message + "\n"


def format_rising_stars_telegram(df_runners, df_stealth, df_extended, spy_ret_20d, options_map):
    spy_line = f"SPY 20j: {spy_ret_20d:+.1f}%" if spy_ret_20d is not None else ""
    fh_note = "Finnhub ✅" if FINNHUB_API_KEY else "Finnhub off"
    message = (
        f"🚀 *AnisTrade — RISING STARS v5*\n"
        f"_{spy_line} | Cap ≥{RUNNER_MIN_MARKET_CAP // 1_000_000}M$ | "
        f"Runners: Vol≥{RUNNER_STRICT_VOL_RATIO}x & jour>0 | {fh_note}_\n\n"
    )

    message += _format_section_by_sector(
        df_runners,
        f"🌟 *RUNNERS* _(volume + momentum jour)_",
        options_map, RUNNER_TOP_ALERTS,
    )
    message += _format_section_by_sector(
        df_stealth,
        f"🕵️ *ACHATS FURTIFS* _(Vol action <{STEALTH_MAX_VOL_RATIO}x, CALL Vol/OI ≥{STEALTH_MIN_OPT_VOL_OI}x)_",
        options_map, STEALTH_TOP_ALERTS, stealth=True,
    )

    if not df_extended.empty:
        message += f"📈 *DÉJÀ EN RUN* _(>{RUNNER_MAX_VAR20:.0f}% 20j)_\n"
        for _, row in df_extended.head(EXTENDED_TOP_ALERTS).iterrows():
            message += f"_{row['Ticker']}_ {row['Var20j']:+.0f}% | {row['Prix']:.0f}$ | Vol:{row['VolRatio']:.1f}x\n"
        message += "\n"

    if df_runners.empty and df_stealth.empty:
        message += "💤 _Aucun signal fort aujourd'hui._\n"
    message += "⚠️ _Pas de garantie +100%. Vérifiez catalyseurs avant trade._"
    return message


def main():
    if not all([TELEGRAM_TOKEN, TELEGRAM_CHAT_ID]):
        raise Exception("Secrets Telegram manquants. Vérifiez vos Secrets GitHub.")

    nasdaq_stocks, _ = fetch_nasdaq_trader_symbols()
    stock_quotes, sources = fetch_screener_universe()
    tickers = build_runner_candidates(stock_quotes, nasdaq_stocks, sources)
    if not tickers:
        log("Aucun candidat screener. Fin.")
        return

    spy_ret_20d = get_spy_benchmark()
    if spy_ret_20d is not None:
        log(f"📊 Benchmark SPY 20j : {spy_ret_20d:+.2f}%")

    df_runners, df_stealth_pool, df_extended = analyze_candidates(tickers, sources, spy_ret_20d)

    options_runners = scan_options_for_df(df_runners, "runners")
    options_stealth = scan_options_for_df(df_stealth_pool, "furtif")
    options_map = {**options_runners, **options_stealth}

    df_stealth = build_stealth_df(df_stealth_pool, options_map)
    df_runners, df_stealth, df_extended = enrich_with_finnhub(df_runners, df_stealth, df_extended)

    message = format_rising_stars_telegram(df_runners, df_stealth, df_extended, spy_ret_20d, options_map)
    send_telegram(message)
    log("🚀 Alerte Rising Stars envoyée !")


if __name__ == "__main__":
    main()
