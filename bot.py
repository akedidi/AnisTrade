import os
import re
import json
import time
import logging
from datetime import datetime, timedelta
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
USER_AGENT = "AnisTradeBot/9.0 (highlights-menu)"

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
BUY_RATINGS = {"buy", "strong_buy", "strongbuy", "outperform", "overweight"}
RUNNER_MAX_DOWNLOAD = 120
HIGHLIGHT_MAX_STOCKS = 4
HIGHLIGHT_MAX_ETFS = 2
STEALTH_SCAN_CAP = 12
EXTENDED_MIN_PRICE = 3.0

MENU_MAX_PER_SECTOR = 3
MENU_MAX_ETF = 3
MENU_MAX_RUNNERS = 8
MENU_MAX_STEALTH = 8
MENU_MAX_EXTENDED = 5

MENU_LABELS = {
    "highlights": "✨ Highlights",
    "actions": "📈 Actions",
    "etfs": "📊 ETFs",
    "runners": "🌟 Runners",
    "furtifs": "🕵️ Furtifs",
    "extended": "📈 Déjà en run",
}

# Commandes du menu natif Telegram (bouton / à côté du champ de saisie)
BOT_COMMANDS = [
    {"command": "start", "description": "S'abonner aux alertes Highlights"},
    {"command": "highlights", "description": "Top actions + ETF"},
    {"command": "actions", "description": "Actions par secteur"},
    {"command": "etfs", "description": "ETF par catégorie"},
    {"command": "runners", "description": "Runners (vol + momentum)"},
    {"command": "furtifs", "description": "Achats furtifs (options)"},
    {"command": "dejarenrun", "description": "Déjà en run (>60% 20j)"},
    {"command": "menu", "description": "Afficher les commandes"},
]
COMMAND_TO_ACTION = {
    "/highlights": "highlights",
    "/actions": "actions",
    "/etfs": "etfs",
    "/runners": "runners",
    "/furtifs": "furtifs",
    "/dejarenrun": "extended",
    "/extended": "extended",
}
MENU_HELP = (
    "📋 <b>Commandes AnisTrade</b>\n"
    "Tapez <b>/</b> ou ouvrez le menu du bot :\n\n"
    "/highlights — top actions + ETF\n"
    "/actions — actions par secteur\n"
    "/etfs — ETF par catégorie\n"
    "/runners — momentum + volume\n"
    "/furtifs — achats furtifs\n"
    "/dejarenrun — déjà en run"
)

STEALTH_MAX_VOL_RATIO = 1.5
STEALTH_MIN_OPT_VOL_OI = 5.0
STEALTH_MIN_VAR1D = -8.0

SCORE_W_MOMENTUM = 0.35
SCORE_W_ANALYST = 0.30
SCORE_W_OPTIONS = 0.25
SCORE_W_SENTIMENT = 0.10

X2_MIN_TARGET_UPSIDE = 50.0
X2_STRONG_TARGET_UPSIDE = 80.0

CHUNK_SIZE = 80
CHUNK_DELAY_SEC = 0.25
FINNHUB_DELAY_SEC = 0.15
NEWS_LOOKBACK_DAYS = 14

SECTOR_ORDER = ["Biotech", "IA", "Sante", "Energie", "Autre"]
SECTOR_LABELS = {
    "Biotech": "🧬 BIOTECH",
    "IA": "🤖 TECH / IA",
    "Sante": "🏥 SANTÉ",
    "Energie": "⚡ ÉNERGIE",
    "Autre": "📦 AUTRE",
}

ETF_GROUPS = {
    "CROISSANCE": ["QQQ", "VUG", "SCHG"],
    "IA": ["BOTZ", "AIQ"],
    "SEMI": ["SOXX", "SMH"],
}
ETF_GROUP_LABELS = {
    "CROISSANCE": "📊 ETF CROISSANCE",
    "IA": "📊 ETF IA",
    "SEMI": "📊 ETF SEMI",
}

FDA_NEGATIVE_KW = (
    "fda reject", "complete response letter", "clinical hold",
    "trial failure", "failed to meet", "did not meet primary",
    "phase 3 fail", "phase iii fail", "halts trial", "drug rejected",
)

CATALYST_RULES = (
    (("pdufa", "fda approval", "fda decision", "fda accepts"), "FDA / PDUFA"),
    (("phase 3", "phase iii", "phase 3 data", "pivotal trial"), "Phase III"),
    (("acquisition", "buyout", "merger", "takeover", "to be acquired"), "Rachat / M&A"),
    (("short squeeze", "high short interest"), "Short squeeze"),
    (("earnings beat", "raises guidance", "upgraded to"), "Résultats / guidance"),
    (("partnership", "licensing deal", "collaboration"), "Partenariat"),
)

OPTIONS_MAX_EXPIRATIONS = 2
OPTIONS_MIN_VOL_OI = 2.0
OPTIONS_MIN_OI = 10
OPTIONS_MIN_VOLUME_STOCK = 100
OPTIONS_OTM_MIN_PCT = 1.02
OPTIONS_DELAY_SEC = 0.25
OPTIONS_CONTRACTS_PER_TICKER = 2

SUBSCRIBERS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "subscribers.json")


def _welcome_message(chat_id):
    return (
        "✅ <b>AnisTrade</b> — abonnement confirmé.\n"
        f"🆔 Votre <b>chat_id</b> : <code>{escape_html(chat_id)}</code>\n\n"
        "Ouvrez le menu du bot (bouton <b>/</b>) pour explorer les signaux.\n"
        "Les <b>Highlights</b> automatiques sont envoyés à chaque alerte planifiée."
    )
SCAN_CACHE_TTL_SEC = 900
_scan_cache = {"ts": 0, "data": None}


def to_yahoo_symbol(symbol: str) -> str:
    return symbol.replace(".", "-")


def log(msg):
    print(msg, flush=True)


def escape_html(text):
    return (
        str(text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _telegram_post_ok(resp):
    try:
        data = resp.json()
    except ValueError:
        return False, resp.text[:400]
    if not data.get("ok"):
        return False, data.get("description", resp.text[:400])
    return True, None


def _default_subscribers():
    return {"chat_ids": [], "update_offset": 0}


def load_subscribers():
    data = _default_subscribers()
    if os.path.exists(SUBSCRIBERS_FILE):
        try:
            with open(SUBSCRIBERS_FILE, encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded.get("chat_ids"), list):
                data["chat_ids"] = [str(c) for c in loaded["chat_ids"]]
            if isinstance(loaded.get("update_offset"), int):
                data["update_offset"] = loaded["update_offset"]
        except (json.JSONDecodeError, OSError) as e:
            log(f"⚠️ subscribers.json illisible : {e}")
    if TELEGRAM_CHAT_ID:
        chat_id = str(TELEGRAM_CHAT_ID)
        if chat_id not in data["chat_ids"]:
            data["chat_ids"].append(chat_id)
    return data


def save_subscribers(data):
    with open(SUBSCRIBERS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def get_subscriber_chat_ids():
    return load_subscribers().get("chat_ids", [])


def _send_raw_telegram(chat_id, text, parse_mode="HTML", reply_markup=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_markup:
        payload["reply_markup"] = reply_markup
    resp = requests.post(url, json=payload, timeout=30)
    ok, err = _telegram_post_ok(resp)
    if not ok:
        raise RuntimeError(f"Telegram {chat_id}: {err}")
    return True


def ensure_telegram_polling():
    """Supprime un éventuel webhook (sinon getUpdates ne reçoit rien)."""
    if not TELEGRAM_TOKEN:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/deleteWebhook"
    try:
        resp = requests.post(url, json={"drop_pending_updates": False}, timeout=15)
        ok, err = _telegram_post_ok(resp)
        if ok:
            log("🔗 Mode polling actif (webhook supprimé)")
        else:
            log(f"⚠️ deleteWebhook : {err}")
    except Exception as e:
        log(f"⚠️ deleteWebhook : {e}")


def register_bot_commands():
    """Enregistre le menu de commandes dans le bot Telegram (pas de clavier canal)."""
    if not TELEGRAM_TOKEN:
        return
    ensure_telegram_polling()
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setMyCommands"
    try:
        resp = requests.post(url, json={"commands": BOT_COMMANDS}, timeout=15)
        ok, err = _telegram_post_ok(resp)
        if ok:
            log("📋 Menu bot enregistré (commandes /)")
        else:
            log(f"⚠️ setMyCommands : {err}")
    except Exception as e:
        log(f"⚠️ setMyCommands : {e}")


def send_to_chat(chat_id, text):
    """Envoie un message à un seul utilisateur (sans clavier reply)."""
    for chunk in _split_telegram_message(text):
        _send_raw_telegram(chat_id, chunk)


def _handle_start(chat_id, data):
    new = 0
    if chat_id not in data["chat_ids"]:
        data["chat_ids"].append(chat_id)
        new = 1
        log(f"📬 Nouvel abonné : {chat_id}")
    try:
        _send_raw_telegram(chat_id, _welcome_message(chat_id), reply_markup={"remove_keyboard": True})
        send_to_chat(chat_id, MENU_HELP)
    except Exception as e:
        log(f"⚠️ Message bienvenue {chat_id}: {e}")
    return new


def _handle_menu_action(chat_id, action_key):
    log(f"📲 Commande '{action_key}' demandée par {chat_id}")
    send_to_chat(chat_id, "⏳ <i>Analyse en cours…</i>")
    try:
        scan = get_scan_data(force=True)
        formatters = {
            "highlights": lambda: format_highlights_telegram(
                scan["stock_highlights"], scan["etf_highlights"], scan["spy_ret_20d"], scan["options_map"],
            ),
            "actions": lambda: format_actions_telegram(scan["df_momentum"], scan["options_map"], scan["spy_ret_20d"]),
            "etfs": lambda: format_etfs_telegram(scan["etf_data"], scan["spy_ret_20d"]),
            "runners": lambda: format_runners_telegram(scan["df_runners"], scan["options_map"], scan["spy_ret_20d"]),
            "furtifs": lambda: format_furtifs_telegram(scan["df_stealth"], scan["options_map"], scan["spy_ret_20d"]),
            "extended": lambda: format_extended_telegram(scan["df_extended"], scan["spy_ret_20d"]),
        }
        send_to_chat(chat_id, formatters[action_key]())
    except Exception as e:
        log(f"⚠️ Commande {action_key} pour {chat_id}: {e}")
        send_to_chat(chat_id, f"⚠️ Erreur : {escape_html(str(e)[:200])}")


def process_telegram_updates(handle_menus=False):
    """Traite /start, abonnements et (optionnel) le menu interactif."""
    if not TELEGRAM_TOKEN:
        return load_subscribers()

    ensure_telegram_polling()
    data = load_subscribers()
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
    params = {"offset": data.get("update_offset", 0), "timeout": 0, "allowed_updates": ["message"]}

    try:
        resp = requests.get(url, params=params, timeout=30)
        ok, err = _telegram_post_ok(resp)
        if not ok:
            log(f"⚠️ getUpdates : {err}")
            return data
        updates = resp.json().get("result", [])
    except Exception as e:
        log(f"⚠️ getUpdates : {e}")
        return data

    new_subs = 0
    for update in updates:
        data["update_offset"] = update["update_id"] + 1
        message = update.get("message")
        if not message:
            continue
        text = (message.get("text") or "").strip()
        chat_id = str(message["chat"]["id"])
        cmd = text.split()[0].split("@")[0].lower() if text else ""

        if cmd == "/start":
            try:
                new_subs += _handle_start(chat_id, data)
            except Exception as e:
                log(f"⚠️ /start pour {chat_id}: {e}")
            continue
        if cmd == "/menu":
            send_to_chat(chat_id, MENU_HELP)
            continue

        if not handle_menus:
            continue

        action_key = COMMAND_TO_ACTION.get(cmd)
        if action_key:
            if chat_id not in data["chat_ids"]:
                data["chat_ids"].append(chat_id)
                new_subs += 1
            try:
                _handle_menu_action(chat_id, action_key)
            except Exception as e:
                log(f"⚠️ Action commande : {e}")

    if updates:
        save_subscribers(data)
        if new_subs:
            log(f"📬 Abonnés : {len(data['chat_ids'])} (+{new_subs} nouveau(x))")
        elif not new_subs:
            log(f"📬 {len(updates)} update(s) traité(s), abonnés : {len(data['chat_ids'])}")
    return data


def _send_telegram_to_chat(message, chat_id):
    chunks = _split_telegram_message(message)
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    for i, chunk in enumerate(chunks, 1):
        sent = False
        for parse_mode in ("HTML", None):
            payload = {
                "chat_id": chat_id,
                "text": chunk,
                "disable_web_page_preview": True,
            }
            if parse_mode:
                payload["parse_mode"] = parse_mode
            try:
                resp = requests.post(url, json=payload, timeout=30)
                ok, err = _telegram_post_ok(resp)
                if ok:
                    sent = True
                    log(
                        f"✅ Telegram → {chat_id} chunk {i}/{len(chunks)} "
                        f"({parse_mode or 'plain'}, {len(chunk)} chars)"
                    )
                    break
                log(f"⚠️ Telegram → {chat_id} chunk {i} ({parse_mode or 'plain'}): {err}")
            except Exception as e:
                log(f"⚠️ Telegram → {chat_id} chunk {i}: {e}")
        if not sent:
            raise RuntimeError(f"Échec envoi Telegram → {chat_id} (chunk {i}/{len(chunks)})")


def send_telegram(message):
    if not message:
        log("⚠️ Telegram : message vide, envoi ignoré")
        return

    chat_ids = get_subscriber_chat_ids()
    if not chat_ids:
        raise RuntimeError(
            "Aucun abonné Telegram. Envoyez /start au bot ou définissez TELEGRAM_CHAT_ID."
        )

    log(f"📤 Envoi à {len(chat_ids)} abonné(s)...")
    for chat_id in chat_ids:
        _send_telegram_to_chat(message, chat_id)


def _split_telegram_message(message, limit=4000):
    if len(message) <= limit:
        return [message]
    chunks, current = [], ""
    for line in message.split("\n"):
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit and current:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks or [message[:limit]]


def classify_sector(sector, industry):
    s = (sector or "").lower()
    i = (industry or "").lower()
    bio_kw = ("biotech", "biotechnology", "genomic", "nanotechnology")
    ai_kw = ("software", "semiconductor", "internet", "cloud", "artificial", "computer", "ai ")
    energy_kw = ("oil", "gas", "energy", "solar", "renewable", "uranium", "coal")

    if any(k in i for k in bio_kw):
        return "Biotech"
    sante_kw = (
        "health information", "medical care", "hospital", "diagnostics",
        "telehealth", "wellness", "healthcare plans", "medical devices",
    )
    if any(k in i for k in sante_kw):
        return "Sante"
    if s == "healthcare":
        return "Biotech" if ("drug" in i or "pharma" in i) and "biotech" in i else (
            "Biotech" if i.startswith("biotech") else "Sante"
        )
    if s == "energy" or any(k in i for k in energy_kw):
        return "Energie"
    if s == "technology" or any(k in i for k in ai_kw):
        return "IA"
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


def annualized_volatility(closes, days=30):
    if closes is None or len(closes) < days + 2:
        return None
    rets = closes.pct_change().dropna().tail(days)
    if len(rets) < 5:
        return None
    return float(rets.std() * (252 ** 0.5) * 100)


def risk_level(beta, short_pct, vol_30d):
    """Risque composite : beta, short interest, volatilité 30j → Faible / Moyen / Élevé."""
    points = 0
    components = 0
    if beta is not None:
        components += 1
        if beta >= 1.8:
            points += 2
        elif beta >= 1.2:
            points += 1
    if short_pct is not None:
        components += 1
        if short_pct >= 20:
            points += 2
        elif short_pct >= 10:
            points += 1
    if vol_30d is not None:
        components += 1
        if vol_30d >= 70:
            points += 2
        elif vol_30d >= 45:
            points += 1
    if components == 0:
        return "Moyen", "🟠"
    avg = points / components
    if avg >= 1.5:
        return "Élevé", "🔴"
    if avg >= 0.75:
        return "Moyen", "🟠"
    return "Faible", "🟢"


def detect_catalyst_from_text(text):
    t = (text or "").lower()
    for keywords, label in CATALYST_RULES:
        if any(kw in t for kw in keywords):
            return label
    return None


def format_earnings_date(ts):
    if not ts:
        return None
    try:
        if isinstance(ts, (int, float)):
            dt = datetime.fromtimestamp(ts)
        else:
            dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return dt.strftime("%d %b")
    except (ValueError, TypeError, OSError):
        return None


def sentiment_score(finnhub):
    if not finnhub:
        return 50
    np_ = finnhub.get("news_pct")
    if np_ is not None:
        return round(min(max(float(np_), 0), 100), 0)
    ns = finnhub.get("news_score")
    if ns is not None:
        return round(min(max(float(ns) * 100, 0), 100), 0)
    return 50


def fetch_analyst_meta(tickers):
    log(f"📊 Métadonnées Yahoo ({len(tickers)} tickers)...")
    meta = {}
    for i, ticker in enumerate(tickers, 1):
        entry = {
            "recommendation": "", "is_buy": False, "target": None,
            "upside_pct": None, "market_cap": None, "sector": "", "industry": "", "category": "Autre",
            "beta": None, "short_pct": None, "earnings_date": None,
        }
        try:
            info = yf.Ticker(to_yahoo_symbol(ticker)).info
            rec = (info.get("recommendationKey") or "").lower()
            target = info.get("targetMeanPrice") or info.get("targetMedianPrice")
            price = info.get("currentPrice") or info.get("regularMarketPrice")
            sector, industry = info.get("sector") or "", info.get("industry") or ""
            beta = info.get("beta")
            short_pct = info.get("shortPercentOfFloat") or info.get("shortRatio")
            if short_pct is not None and short_pct <= 1:
                short_pct = float(short_pct) * 100
            earnings_ts = info.get("earningsTimestamp") or info.get("earningsTimestampStart")
            entry.update({
                "recommendation": rec,
                "is_buy": rec in BUY_RATINGS,
                "market_cap": info.get("marketCap"),
                "sector": sector,
                "industry": industry,
                "category": classify_sector(sector, industry),
                "beta": float(beta) if beta is not None else None,
                "short_pct": float(short_pct) if short_pct is not None else None,
                "earnings_date": format_earnings_date(earnings_ts),
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
    """Finnhub — finalistes : reco, news, social, price target, alertes FDA."""
    empty = {
        "rec_buy_pct": None, "news_score": None, "news_pct": None,
        "bullish_pct": None, "bearish_pct": None,
        "social_score": None, "social_pct": None,
        "price_target": None, "price_target_upside": None,
        "negative_fda_news": False, "catalyst": None, "finnhub_ok": False,
    }
    if not FINNHUB_API_KEY:
        log("📊 Finnhub : clé absente — sentiment ignoré")
        return {t: dict(empty) for t in tickers}

    log(f"📊 Finnhub sentiment ({len(tickers)} finalistes)...")
    meta = {}
    today = datetime.now().date()
    news_from = (today - timedelta(days=NEWS_LOOKBACK_DAYS)).isoformat()
    social_from = (today - timedelta(days=7)).isoformat()
    news_to = social_to = today.isoformat()

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
                ns = news.get("companyNewsScore")
                entry["news_score"] = ns
                if ns is not None:
                    entry["news_pct"] = round(float(ns) * 100, 0)
                sent = news.get("sentiment") or {}
                entry["bullish_pct"] = sent.get("bullishPercent")
                entry["bearish_pct"] = sent.get("bearishPercent")
            time.sleep(FINNHUB_DELAY_SEC)

            social_resp = requests.get(
                f"{FINNHUB_BASE}/stock/social-sentiment",
                params={**params, "from": social_from, "to": social_to},
                timeout=10,
            )
            if social_resp.ok:
                social_data = social_resp.json()
                items = social_data if isinstance(social_data, list) else social_data.get("data", [])
                if items:
                    scores = [float(x.get("score", 0) or 0) for x in items if x.get("score") is not None]
                    if scores:
                        avg = sum(scores) / len(scores)
                        entry["social_score"] = round(avg, 3)
                        entry["social_pct"] = round(min(max((avg + 1) / 2 * 100, 0), 100), 0)
            time.sleep(FINNHUB_DELAY_SEC)

            pt_resp = requests.get(f"{FINNHUB_BASE}/stock/price-target", params=params, timeout=10)
            if pt_resp.ok:
                pt = pt_resp.json()
                target = pt.get("targetMean") or pt.get("targetHigh")
                if target:
                    entry["price_target"] = float(target)
            time.sleep(FINNHUB_DELAY_SEC)

            cn_resp = requests.get(
                f"{FINNHUB_BASE}/company-news",
                params={**params, "from": news_from, "to": news_to},
                timeout=10,
            )
            if cn_resp.ok:
                for article in cn_resp.json() or []:
                    headline = article.get("headline") or ""
                    summary = article.get("summary") or ""
                    text = f"{headline} {summary}"
                    text_l = text.lower()
                    if any(kw in text_l for kw in FDA_NEGATIVE_KW):
                        entry["negative_fda_news"] = True
                    if not entry["catalyst"]:
                        cat = detect_catalyst_from_text(text)
                        if cat:
                            entry["catalyst"] = cat
                            if cat == "Résultats / guidance" and headline:
                                entry["catalyst"] = f"{cat} — {headline[:60]}"
            time.sleep(FINNHUB_DELAY_SEC)

            earn_resp = requests.get(
                f"{FINNHUB_BASE}/calendar/earnings",
                params={**params, "from": today.isoformat(), "to": (today + timedelta(days=120)).isoformat()},
                timeout=10,
            )
            if earn_resp.ok and not entry.get("catalyst"):
                for ev in earn_resp.json() or []:
                    if (ev.get("symbol") or "").upper() == ticker:
                        ed = ev.get("date") or ev.get("period")
                        entry["catalyst"] = f"Résultats le {ed}" if ed else "Résultats à venir"
                        break

            entry["finnhub_ok"] = True
        except Exception as e:
            log(f"   ⚠️ Finnhub {ticker}: {e}")
        meta[ticker] = entry
        if i % 8 == 0:
            time.sleep(0.5)
    ok = sum(1 for m in meta.values() if m["finnhub_ok"])
    log(f"   Finnhub OK : {ok}/{len(tickers)}")
    return meta


def _passes_market_cap(analyst):
    cap = analyst.get("market_cap")
    return cap is not None and RUNNER_MIN_MARKET_CAP <= cap <= RUNNER_MAX_MARKET_CAP


def momentum_score(var_1d, var_5d, var_20d, rs_20d, vol_ratio, is_shorted, accelerating):
    score = 0.0
    if var_5d is not None and var_5d > 0:
        score += min(var_5d * 2.0, 30)
    if var_20d is not None and RUNNER_MIN_VAR20 <= var_20d <= RUNNER_MAX_VAR20:
        score += min(var_20d * 1.2, 40)
    if var_1d is not None:
        if var_1d > 0:
            score += min(var_1d * 2.0, 20)
        elif var_1d <= STEALTH_MIN_VAR1D:
            score -= 35
        elif var_1d < 0:
            score -= 12
    if rs_20d is not None and rs_20d > 0:
        score += min(rs_20d * 0.6, 15)
    if vol_ratio is not None and vol_ratio >= RUNNER_STRICT_VOL_RATIO:
        score += min((vol_ratio - 1) * 10, 15)
    if is_shorted:
        score += 5
    if accelerating:
        score += 8
    return round(min(max(score, 0), 100), 0)


def analyst_score(is_buy, target_upside, price, finnhub):
    score = 0.0
    if is_buy:
        score += 40
    upside = target_upside
    if finnhub and finnhub.get("price_target") and price and float(price) > 0:
        fh_up = (finnhub["price_target"] / float(price) - 1) * 100
        upside = fh_up if upside is None else max(upside, fh_up)
    if upside is not None:
        if upside >= 100:
            score += 40
        elif upside >= 50:
            score += 30
        elif upside >= ANALYST_TARGET_MIN_UPSIDE:
            score += 20
        elif upside > 0:
            score += 10
    if finnhub:
        rb = finnhub.get("rec_buy_pct")
        if rb is not None:
            score += min(rb * 0.35, 25)
        bp = finnhub.get("bearish_pct")
        if bp is not None and bp >= 0.55:
            score -= 20
        if finnhub.get("negative_fda_news"):
            score -= 30
    return round(min(max(score, 0), 100), 0)


def options_score(top_vol_oi):
    if not top_vol_oi:
        return 0
    if top_vol_oi >= 15:
        return 100
    if top_vol_oi >= 10:
        return 85
    if top_vol_oi >= STEALTH_MIN_OPT_VOL_OI:
        return round(min(50 + (top_vol_oi - STEALTH_MIN_OPT_VOL_OI) * 7, 80), 0)
    return round(min(top_vol_oi * 10, 45), 0)


def global_score(momentum, analyst, options, sentiment):
    return round(
        SCORE_W_MOMENTUM * (momentum or 0)
        + SCORE_W_ANALYST * (analyst or 0)
        + SCORE_W_OPTIONS * (options or 0)
        + SCORE_W_SENTIMENT * (sentiment or 50),
        0,
    )


def compute_scores(var_1d, var_5d, var_20d, rs_20d, vol_ratio, is_shorted, accelerating,
                   is_buy, target_upside, price, finnhub, top_vol_oi=None):
    accelerating = accelerating or (
        var_5d is not None and var_20d is not None and var_5d > 0 and var_5d > (var_20d / 4)
    )
    m = momentum_score(var_1d, var_5d, var_20d, rs_20d, vol_ratio, is_shorted, accelerating)
    a = analyst_score(is_buy, target_upside, price, finnhub)
    o = options_score(top_vol_oi)
    s = sentiment_score(finnhub)
    return m, a, o, s, global_score(m, a, o, s)


def get_spy_benchmark():
    closes, _, ok, _ = download_chunked(["SPY"], period="3mo")
    if ok == 0 or "SPY" not in closes:
        return None
    return pct_return(closes["SPY"], 20)


def _build_row(ticker, price, var_1d, var_5d, var_20d, rs_20d, vol_ratio, analyst, sources, finnhub, vol_30d=None, top_vol_oi=None):
    is_shorted = ticker in sources["shorted"]
    accelerating = var_5d is not None and var_20d is not None and var_5d > 0 and var_5d > (var_20d / 4)
    fh = finnhub or {}
    m, a, o, s, g = compute_scores(
        var_1d, var_5d, var_20d, rs_20d, vol_ratio, is_shorted, accelerating,
        analyst.get("is_buy", False), analyst.get("upside_pct"), price, fh, top_vol_oi,
    )
    beta, short_pct = analyst.get("beta"), analyst.get("short_pct")
    risk_label, risk_emoji = risk_level(beta, short_pct, vol_30d)
    catalyst = fh.get("catalyst")
    if not catalyst and analyst.get("earnings_date"):
        catalyst = f"Résultats le {analyst['earnings_date']}"
    return {
        "Ticker": ticker,
        "Prix": price,
        "Var1j": var_1d or 0,
        "Var5j": var_5d or 0,
        "Var20j": var_20d or 0,
        "RS20j": rs_20d or 0,
        "VolRatio": vol_ratio,
        "Vol30d": vol_30d,
        "Beta": beta,
        "ShortPct": short_pct,
        "Shorted": is_shorted,
        "AnalystBuy": analyst.get("is_buy", False),
        "TargetUpside": analyst.get("upside_pct"),
        "Category": analyst.get("category", "Autre"),
        "MomentumScore": m,
        "AnalystScore": a,
        "OptionsScore": o,
        "SentimentScore": s,
        "Score": g,
        "RiskLabel": risk_label,
        "RiskEmoji": risk_emoji,
        "Catalyst": catalyst,
        "TopVolOI": top_vol_oi,
        "NewsScore": fh.get("news_score"),
        "NewsPct": fh.get("news_pct"),
        "RecBuyPct": fh.get("rec_buy_pct"),
        "SocialPct": fh.get("social_pct"),
        "BullishPct": fh.get("bullish_pct"),
        "NegativeFdaNews": fh.get("negative_fda_news", False),
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
        vol_30d = annualized_volatility(c, 30)
        prior_vol = v.iloc[:-1]
        avg_vol = float(prior_vol.tail(20).mean()) if len(prior_vol) >= 5 else float(prior_vol.mean())
        vol_ratio = float(v.iloc[-1]) / avg_vol if avg_vol > 0 else 0
        rs_20d = (var_20d - spy_ret_20d) if (var_20d is not None and spy_ret_20d is not None) else None

        if var_20d is not None and var_20d > RUNNER_MAX_VAR20:
            if price >= EXTENDED_MIN_PRICE:
                extended.append(_build_row(ticker, price, var_1d, var_5d, var_20d, rs_20d, vol_ratio, analyst, sources, {}, vol_30d))
            continue

        if var_20d is None or var_20d < RUNNER_MIN_VAR20 or (rs_20d is not None and rs_20d < 0):
            continue

        momentum_pool.append(_build_row(ticker, price, var_1d, var_5d, var_20d, rs_20d, vol_ratio, analyst, sources, {}, vol_30d))

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
    df_momentum = pd.DataFrame(momentum_pool)
    return df_runners, df_stealth_pool, df_extended, df_momentum


def enrich_with_finnhub(df_runners, df_stealth, df_extended, df_momentum=None):
    tickers = set()
    for df in (df_runners, df_stealth, df_extended, df_momentum):
        if df is not None and not df.empty:
            tickers.update(df["Ticker"].tolist())
    if not tickers:
        return df_runners, df_stealth, df_extended, df_momentum

    fh = fetch_finnhub_meta(sorted(tickers))

    def _apply(df):
        if df is None or df.empty:
            return df
        rows = []
        for _, row in df.iterrows():
            r = row.to_dict()
            meta = fh.get(r["Ticker"], {})
            top_vol_oi = r.get("TopVolOI")
            m, a, o, s, g = compute_scores(
                r["Var1j"], r["Var5j"], r["Var20j"], r["RS20j"], r["VolRatio"],
                r["Shorted"], r["Var5j"] > 0 and r["Var5j"] > r["Var20j"] / 4,
                r["AnalystBuy"], r["TargetUpside"], r["Prix"], meta, top_vol_oi,
            )
            catalyst = meta.get("catalyst") or r.get("Catalyst")
            r.update({
                "MomentumScore": m, "AnalystScore": a, "OptionsScore": o,
                "SentimentScore": s, "Score": g,
                "TopVolOI": top_vol_oi,
                "NewsScore": meta.get("news_score"),
                "NewsPct": meta.get("news_pct"),
                "RecBuyPct": meta.get("rec_buy_pct"),
                "SocialPct": meta.get("social_pct"),
                "BullishPct": meta.get("bullish_pct"),
                "NegativeFdaNews": meta.get("negative_fda_news", False),
                "Catalyst": catalyst,
            })
            rows.append(r)
        out = pd.DataFrame(rows)
        if not out.empty:
            out = out.sort_values(by=["Score", "Var20j"], ascending=False)
        return out

    return _apply(df_runners), _apply(df_stealth), _apply(df_extended), _apply(df_momentum)


def build_x2_df(df_momentum):
    if df_momentum is None or df_momentum.empty:
        return pd.DataFrame()
    rows = []
    for _, row in df_momentum.iterrows():
        up = row.get("TargetUpside")
        rb = row.get("RecBuyPct")
        qualifies = False
        if up is not None and up >= X2_STRONG_TARGET_UPSIDE:
            qualifies = True
        elif up is not None and up >= X2_MIN_TARGET_UPSIDE and (row.get("AnalystBuy") or (rb and rb >= 60)):
            qualifies = True
        if qualifies:
            rows.append(row.to_dict())
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    return df.sort_values(by=["AnalystScore", "TargetUpside", "Score"], ascending=False)


def analyze_etf_watchlist(spy_ret_20d):
    symbols = [s for group in ETF_GROUPS.values() for s in group]
    log(f"📊 ETF watchlist ({len(symbols)} tickers)...")
    closes, _, ok, fail = download_chunked(symbols, period="3mo")
    log(f"   ETF : ✅ {ok} | ❌ {fail}")
    results = {}
    for group, tickers in ETF_GROUPS.items():
        rows = []
        for sym in tickers:
            c = closes.get(sym)
            if c is None:
                continue
            c = c.dropna()
            if len(c) < 22:
                continue
            price = float(c.iloc[-1])
            var_1d = pct_return(c, 1) or 0
            var_5d = pct_return(c, 5) or 0
            var_20d = pct_return(c, 20) or 0
            rs_20d = (var_20d - spy_ret_20d) if spy_ret_20d is not None else None
            rows.append({
                "Ticker": sym, "Prix": price,
                "Var1j": var_1d, "Var5j": var_5d, "Var20j": var_20d,
                "RS20j": rs_20d or 0,
            })
        if rows:
            results[group] = sorted(rows, key=lambda x: x["Var20j"], reverse=True)
    return results


def apply_options_scores(df, options_map):
    if df.empty:
        return df
    rows = []
    for _, row in df.iterrows():
        r = row.to_dict()
        top_vol_oi = None
        contracts = options_map.get(r["Ticker"])
        if contracts:
            top_vol_oi = contracts[0]["vol_oi"]
        m, a, o, s, g = compute_scores(
            r["Var1j"], r["Var5j"], r["Var20j"], r["RS20j"], r["VolRatio"],
            r["Shorted"], r["Var5j"] > 0 and r["Var5j"] > r["Var20j"] / 4,
            r["AnalystBuy"], r["TargetUpside"], r["Prix"], {}, top_vol_oi,
        )
        r.update({
            "MomentumScore": m, "AnalystScore": a, "OptionsScore": o,
            "SentimentScore": s, "Score": g, "TopVolOI": top_vol_oi,
        })
        rows.append(r)
    out = pd.DataFrame(rows)
    return out.sort_values(by=["Score", "Var20j"], ascending=False) if not out.empty else out


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
        if row["Var1j"] <= STEALTH_MIN_VAR1D:
            continue
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


def build_stock_highlights(df_runners, df_stealth, df_x2):
    """Top actions uniques — max HIGHLIGHT_MAX_STOCKS."""
    best = {}
    for signal, df in (("runner", df_runners), ("furtif", df_stealth), ("x2", df_x2)):
        if df is None or df.empty:
            continue
        for _, row in df.iterrows():
            r = row.to_dict()
            r["Signal"] = signal
            ticker = r["Ticker"]
            if ticker not in best or r.get("Score", 0) > best[ticker].get("Score", 0):
                best[ticker] = r
    ranked = sorted(best.values(), key=lambda x: x.get("Score", 0), reverse=True)
    return ranked[:HIGHLIGHT_MAX_STOCKS]


def pick_etf_highlights(etf_data):
    """Top ETF par RS 20j — max HIGHLIGHT_MAX_ETFS."""
    rows = []
    for group, items in (etf_data or {}).items():
        for row in items:
            r = dict(row)
            r["Group"] = group
            rows.append(r)
    rows.sort(key=lambda x: x.get("RS20j", 0), reverse=True)
    return rows[:HIGHLIGHT_MAX_ETFS]


def _safe_num(val, default=0):
    if val is None:
        return default
    try:
        if pd.isna(val):
            return default
    except (TypeError, ValueError):
        pass
    return val


def _format_compact_stock(row, options_map=None, stealth=False):
    """Une ligne compacte — ticker en gras."""
    ticker = escape_html(row["Ticker"])
    score = int(_safe_num(row.get("Score"), 0))
    risk = row.get("RiskEmoji", "🟠")
    parts = [f"<b>{ticker}</b>", f"{score}{risk}", f"20j {_safe_num(row['Var20j']):+.0f}%"]
    rb = row.get("RecBuyPct")
    up = row.get("TargetUpside")
    if rb is not None and not pd.isna(rb):
        parts.append(f"FH {rb:.0f}%")
    elif up is not None and not pd.isna(up):
        parts.append(f"Tgt +{up:.0f}%")
    if options_map:
        contracts = options_map.get(row["Ticker"])
        if contracts:
            icon = "🕵️" if stealth else "🐳"
            parts.append(f"{icon}{contracts[0]['vol_oi']:.1f}x")
    return " | ".join(parts) + "\n"


def _spy_header(spy_ret_20d):
    if spy_ret_20d is not None and not (isinstance(spy_ret_20d, float) and pd.isna(spy_ret_20d)):
        return f"<i>SPY 20j: {spy_ret_20d:+.1f}%</i>\n"
    return ""


def format_actions_telegram(df_momentum, options_map, spy_ret_20d):
    message = f"<b>{MENU_LABELS['actions']}</b>\n{_spy_header(spy_ret_20d)}"
    if df_momentum is None or df_momentum.empty:
        return message + "<i>Aucune action qualifiée.</i>\n"
    df = df_momentum.sort_values(by=["Score", "Var20j"], ascending=False)
    grouped = {cat: [] for cat in SECTOR_ORDER}
    for _, row in df.iterrows():
        grouped.setdefault(row.get("Category", "Autre"), []).append(row)
    for cat in SECTOR_ORDER:
        rows = grouped.get(cat, [])[:MENU_MAX_PER_SECTOR]
        if not rows:
            continue
        message += f"\n<b>{SECTOR_LABELS[cat]}</b>\n"
        for row in rows:
            message += _format_compact_stock(row, options_map)
    return message + "\n⚠️ <i>Pas un conseil d'investissement.</i>"


def format_etfs_telegram(etf_data, spy_ret_20d):
    message = f"<b>{MENU_LABELS['etfs']}</b>\n{_spy_header(spy_ret_20d)}"
    if not etf_data:
        return message + "<i>N/A</i>\n"
    for group in ("CROISSANCE", "IA", "SEMI"):
        rows = (etf_data.get(group) or [])[:MENU_MAX_ETF]
        if not rows:
            continue
        message += f"\n<b>{ETF_GROUP_LABELS[group]}</b>\n"
        for row in rows:
            message += _format_highlight_etf(row)
    return message + "\n⚠️ <i>Pas un conseil d'investissement.</i>"


def format_runners_telegram(df_runners, options_map, spy_ret_20d):
    message = f"<b>{MENU_LABELS['runners']}</b>\n{_spy_header(spy_ret_20d)}"
    message += f"<i>Vol &gt;= {RUNNER_STRICT_VOL_RATIO}x et jour vert</i>\n"
    if df_runners is None or df_runners.empty:
        return message + "<i>Aucun runner aujourd'hui.</i>\n"
    for _, row in df_runners.head(MENU_MAX_RUNNERS).iterrows():
        message += _format_compact_stock(row, options_map)
    return message + "\n⚠️ <i>Pas un conseil d'investissement.</i>"


def format_furtifs_telegram(df_stealth, options_map, spy_ret_20d):
    message = f"<b>🕵️ ACHATS FURTIFS</b>\n{_spy_header(spy_ret_20d)}"
    message += f"<i>Vol &lt; {STEALTH_MAX_VOL_RATIO}x, CALL Vol/OI &gt;= {STEALTH_MIN_OPT_VOL_OI}x</i>\n"
    if df_stealth is None or df_stealth.empty:
        return message + "<i>Aucun signal furtif.</i>\n"
    for _, row in df_stealth.head(MENU_MAX_STEALTH).iterrows():
        message += _format_compact_stock(row, options_map, stealth=True)
    return message + "\n⚠️ <i>Pas un conseil d'investissement.</i>"


def format_extended_telegram(df_extended, spy_ret_20d):
    message = f"<b>📈 DÉJÀ EN RUN</b>\n{_spy_header(spy_ret_20d)}"
    message += f"<i>&gt; {RUNNER_MAX_VAR20:.0f}% sur 20j — trop tard pour x2</i>\n"
    if df_extended is None or df_extended.empty:
        return message + "<i>Aucune action étirée.</i>\n"
    for _, row in df_extended.head(MENU_MAX_EXTENDED).iterrows():
        message += (
            f"<b>{escape_html(row['Ticker'])}</b> "
            f"{row['Var20j']:+.0f}% | {row['Prix']:.0f}$ | Vol {row['VolRatio']:.1f}x\n"
        )
    return message + "\n⚠️ <i>Pas un conseil d'investissement.</i>"


def run_market_scan():
    """Pipeline complet — données pour tous les menus."""
    nasdaq_stocks, _ = fetch_nasdaq_trader_symbols()
    stock_quotes, sources = fetch_screener_universe()
    tickers = build_runner_candidates(stock_quotes, nasdaq_stocks, sources)
    if not tickers:
        raise RuntimeError("Aucun candidat screener.")

    spy_ret_20d = get_spy_benchmark()
    if spy_ret_20d is not None:
        log(f"📊 Benchmark SPY 20j : {spy_ret_20d:+.2f}%")

    df_runners, df_stealth_pool, df_extended, df_momentum = analyze_candidates(tickers, sources, spy_ret_20d)

    stealth_scan = df_stealth_pool.head(STEALTH_SCAN_CAP) if not df_stealth_pool.empty else df_stealth_pool
    options_runners = scan_options_for_df(df_runners, "runners")
    options_stealth = scan_options_for_df(stealth_scan, "furtif")
    options_map = {**options_runners, **options_stealth}

    df_runners = apply_options_scores(df_runners, options_map)
    df_stealth = build_stealth_df(stealth_scan, options_map)
    df_stealth = apply_options_scores(df_stealth, options_map)

    df_runners, df_stealth, df_extended, df_momentum = enrich_with_finnhub(
        df_runners, df_stealth, df_extended, df_momentum,
    )
    df_x2 = build_x2_df(df_momentum)

    stock_highlights = build_stock_highlights(df_runners, df_stealth, df_x2)
    for row in stock_highlights:
        if row["Ticker"] not in options_map:
            extra = scan_options_for_df(pd.DataFrame([row]), "highlights")
            options_map.update(extra)

    etf_data = analyze_etf_watchlist(spy_ret_20d)
    etf_highlights = pick_etf_highlights(etf_data)

    log(
        f"✨ Scan : {len(stock_highlights)} highlights | "
        f"{len(df_runners)} runners | {len(df_stealth)} furtifs | {len(df_extended)} étirés"
    )
    return {
        "spy_ret_20d": spy_ret_20d,
        "df_runners": df_runners,
        "df_stealth": df_stealth,
        "df_extended": df_extended,
        "df_momentum": df_momentum,
        "df_x2": df_x2,
        "options_map": options_map,
        "etf_data": etf_data,
        "stock_highlights": stock_highlights,
        "etf_highlights": etf_highlights,
    }


def get_scan_data(force=False):
    global _scan_cache
    if (
        not force
        and _scan_cache["data"] is not None
        and time.time() - _scan_cache["ts"] < SCAN_CACHE_TTL_SEC
    ):
        log("📦 Scan en cache (réutilisé)")
        return _scan_cache["data"]
    data = run_market_scan()
    _scan_cache = {"ts": time.time(), "data": data}
    return data


def _format_highlight_stock(row, options_map):
    ticker = escape_html(row["Ticker"])
    risk = row.get("RiskEmoji", "🟠")
    score = int(_safe_num(row.get("Score"), 0))
    parts = [f"<b>{ticker}</b>", f"Score {score} {risk}", f"20j {_safe_num(row['Var20j']):+.0f}%"]

    rb = row.get("RecBuyPct")
    up = row.get("TargetUpside")
    if rb is not None:
        parts.append(f"FH {rb:.0f}%")
    elif up is not None:
        parts.append(f"Target +{up:.0f}%")

    news = row.get("SentimentScore")
    if news is not None and news != 50:
        parts.append(f"News {news:.0f}")

    contracts = options_map.get(row["Ticker"])
    if contracts:
        icon = "🕵️" if row.get("Signal") == "furtif" else "🐳"
        parts.append(f"{icon} {contracts[0]['vol_oi']:.1f}x")

    line = " | ".join(parts)
    catalyst = row.get("Catalyst")
    if catalyst:
        line += f"\n📰 {escape_html(str(catalyst)[:55])}"
    return line + "\n"


def _format_highlight_etf(row, best=False):
    star = " ⭐" if best else ""
    return (
        f"<b>{escape_html(row['Ticker'])}</b>{star} "
        f"20j {row['Var20j']:+.1f}% | RS {row.get('RS20j', 0):+.1f}% | {row['Prix']:.0f}$\n"
    )


def format_highlights_telegram(stock_highlights, etf_highlights, spy_ret_20d, options_map):
    message = "🚀 <b>AnisTrade — Highlights</b>\n"
    if spy_ret_20d is not None and not (isinstance(spy_ret_20d, float) and pd.isna(spy_ret_20d)):
        message += f"<i>SPY 20j: {spy_ret_20d:+.1f}%</i>\n"
    message += f"\n<b>📈 ACTIONS</b> <i>(top {HIGHLIGHT_MAX_STOCKS})</i>\n"
    if stock_highlights:
        for row in stock_highlights:
            message += _format_highlight_stock(row, options_map)
    else:
        message += "<i>Rien de convaincant aujourd'hui.</i>\n"

    message += f"\n<b>📊 ETF</b> <i>(top {HIGHLIGHT_MAX_ETFS})</i>\n"
    if etf_highlights:
        for i, row in enumerate(etf_highlights):
            message += _format_highlight_etf(row, best=(i == 0))
    else:
        message += "<i>N/A</i>\n"

    message += "\n⚠️ <i>Pas un conseil d'investissement.</i>"
    return message


def main():
    if not TELEGRAM_TOKEN:
        raise Exception("Secret TELEGRAM_TOKEN manquant. Vérifiez vos Secrets GitHub.")

    register_bot_commands()
    process_telegram_updates(handle_menus=True)
    if not get_subscriber_chat_ids():
        raise Exception(
            "Aucun abonné Telegram. Envoyez /start au bot ou définissez TELEGRAM_CHAT_ID."
        )

    scan = run_market_scan()
    message = format_highlights_telegram(
        scan["stock_highlights"], scan["etf_highlights"], scan["spy_ret_20d"], scan["options_map"],
    )
    log(f"📨 Message Highlights : {len(message)} caractères → tous les abonnés")
    send_telegram(message)
    log("🚀 Alerte Highlights envoyée !")


def run_bot_polling():
    if not TELEGRAM_TOKEN:
        raise SystemExit("TELEGRAM_TOKEN manquant")
    register_bot_commands()
    log("🤖 Bot AnisTrade — commandes / (Ctrl+C pour arrêter)")
    while True:
        process_telegram_updates(handle_menus=True)
        time.sleep(2)


def run_poll_once():
    """Traite les messages Telegram en attente (sans scan marché)."""
    if not TELEGRAM_TOKEN:
        raise SystemExit("TELEGRAM_TOKEN manquant")
    register_bot_commands()
    data = load_subscribers()
    log(f"📡 Poll Telegram (offset={data.get('update_offset', 0)})…")
    data = process_telegram_updates(handle_menus=True)
    log(f"📬 Abonnés enregistrés : {len(data.get('chat_ids', []))} (offset={data.get('update_offset', 0)})")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] in ("--poll-subscribers", "--bot"):
        run_bot_polling()
    elif len(sys.argv) > 1 and sys.argv[1] == "--poll-once":
        run_poll_once()
    else:
        main()
