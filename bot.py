import os
import re
import json
import time
import logging
import pickle
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
import requests
import warnings
import pandas as pd
import yfinance as yf

warnings.simplefilter(action="ignore", category=FutureWarning)
logging.getLogger("yfinance").setLevel(logging.CRITICAL)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY")
WORKER_SUBSCRIBERS_URL = os.getenv("WORKER_SUBSCRIBERS_URL")
WORKER_API_SECRET = os.getenv("WORKER_API_SECRET")

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
RUNNER_MAX_PRICE = 2000.0
RUNNER_MIN_MARKET_CAP = 50_000_000
RUNNER_MIN_VAR20 = 15.0
RUNNER_MAX_VAR20 = 60.0
RUNNER_STRICT_VOL_RATIO = 1.5
ANALYST_TARGET_MIN_UPSIDE = 30.0
BUY_RATINGS = {"buy", "strong_buy", "strongbuy", "outperform", "overweight"}
RUNNER_MAX_DOWNLOAD = 120
HIGHLIGHT_MAX_STOCKS = 4
HIGHLIGHT_X2_SLOTS = 2
HIGHLIGHT_RUNNER_SLOTS = 2
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
STEALTH_MIN_OPT_VOL_OI = 3.0
STEALTH_MIN_VAR1D = -8.0
ETF_MIN_RS20 = 0.0

SCORE_W_MOMENTUM = 0.35
SCORE_W_ANALYST = 0.30
SCORE_W_OPTIONS = 0.25
SCORE_W_SENTIMENT = 0.10

# Highlights / x2 : favoriser entrée early (15–30 % idéal), pas déjà étendu
HIGHLIGHT_MIN_FH_PCT = 30.0
HIGHLIGHT_RUNNER_MIN_TARGET_UPSIDE = 15.0
HIGHLIGHT_SWEET_VAR20_MAX = 35.0
HIGHLIGHT_MAX_VAR20_HIGHLIGHTS = 48.0
WHALE_MAX_VAR20 = 45.0
WHALE_MIN_VAR1D = 0.5

META_CACHE_TTL_SEC = 3600
SCAN_DISK_CACHE_TTL_SEC = 900
OPTIONS_SCAN_MAX_TICKERS = 15
FINNHUB_WORKERS = 6
YAHOO_META_WORKERS = 8
NEWS_CATALYST_MAX_AGE_DAYS = 7
BIOTECH_PRE_CATALYST_MAX_VAR20 = 30.0
BIOTECH_PRE_CATALYST_SCAN_CAP = 25
HTTP_RETRIES = 2

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
STEALTH_OPTIONS_MIN_VOL_OI = 2.0
STEALTH_OPTIONS_MIN_VOLUME = 50
STEALTH_OPTIONS_OTM_MIN_PCT = 1.01
STEALTH_OPTIONS_MIN_OI = 5
OPTIONS_DELAY_SEC = 0.25
OPTIONS_CONTRACTS_PER_TICKER = 2

SUBSCRIBERS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "subscribers.json")
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache")
SCAN_DISK_CACHE_FILE = os.path.join(CACHE_DIR, "market_scan.pkl")


def _welcome_message(chat_id):
    return (
        "✅ <b>AnisTrade</b> — abonnement confirmé.\n"
        f"🆔 Votre <b>chat_id</b> : <code>{escape_html(chat_id)}</code>\n\n"
        "Ouvrez le menu du bot (bouton <b>/</b>) pour explorer les signaux.\n"
        "Les <b>Highlights</b> automatiques sont envoyés à chaque alerte planifiée."
    )
SCAN_CACHE_TTL_SEC = 900
_scan_cache = {"ts": 0, "data": None}


def _load_pickle_cache(path, ttl_sec):
    try:
        if not os.path.isfile(path):
            return None
        if time.time() - os.path.getmtime(path) > ttl_sec:
            return None
        with open(path, "rb") as f:
            return pickle.load(f)
    except Exception:
        return None


def _save_pickle_cache(path, data):
    os.makedirs(os.path.dirname(path) or CACHE_DIR, exist_ok=True)
    with open(path, "wb") as f:
        pickle.dump(data, f)


def _http_get(url, params=None, timeout=10, retries=HTTP_RETRIES):
    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, params=params, timeout=timeout)
            if resp.ok and resp.content:
                return resp
        except requests.RequestException:
            pass
        if attempt < retries:
            time.sleep(0.35 * (attempt + 1))
    return None


def _response_json(resp, default=None):
    """Parse JSON sans planter si le corps est vide ou invalide."""
    if resp is None:
        return default
    try:
        if not resp.content:
            return default
        return resp.json()
    except (ValueError, json.JSONDecodeError):
        return default


def _safe_num(val, default=0):
    if val is None:
        return default
    try:
        if pd.isna(val):
            return default
    except (TypeError, ValueError):
        pass
    return val


def _is_valid_num(val):
    if val is None:
        return False
    try:
        return not pd.isna(val) and float(val) == float(val)
    except (TypeError, ValueError):
        return False


def _score_component(val):
    if not _is_valid_num(val):
        return 0.0
    return max(0.0, float(val))


def _finnhub_meta_from_row(row):
    """Reconstruit les métadonnées Finnhub déjà stockées dans une ligne."""
    meta = {}
    for key, row_key in (
        ("rec_buy_pct", "RecBuyPct"),
        ("news_score", "NewsScore"),
        ("news_pct", "NewsPct"),
        ("bullish_pct", "BullishPct"),
        ("bearish_pct", "BearishPct"),
        ("social_pct", "SocialPct"),
        ("negative_fda_news", "NegativeFdaNews"),
        ("catalyst", "Catalyst"),
        ("catalyst_article_date", "CatalystArticleDate"),
    ):
        val = row.get(row_key)
        if val is None or (isinstance(val, float) and pd.isna(val)):
            continue
        meta[key] = val
    return meta


def _fmt_pct(val, decimals=0, signed=True):
    if not _is_valid_num(val):
        return None
    v = float(val)
    if signed:
        return f"{v:+.{decimals}f} %"
    return f"{v:.{decimals}f} %"


def _effective_target_upside(price, yahoo_upside, fh_meta):
    upsides = []
    if yahoo_upside is not None and not pd.isna(yahoo_upside):
        upsides.append(float(yahoo_upside))
    pt = (fh_meta or {}).get("price_target")
    if pt and price and float(price) > 0:
        upsides.append((float(pt) / float(price) - 1) * 100)
    return max(upsides) if upsides else None


def _row_qualifies_x2(row):
    up = row.get("TargetUpside")
    rb = row.get("RecBuyPct")
    if up is not None and not pd.isna(up) and float(up) >= X2_STRONG_TARGET_UPSIDE:
        return True
    if up is not None and not pd.isna(up) and float(up) >= X2_MIN_TARGET_UPSIDE:
        if row.get("AnalystBuy") or (rb is not None and not pd.isna(rb) and float(rb) >= 60):
            return True
    return False


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
        data = _response_json(resp, default={})
    except Exception:
        return False, (resp.text or "")[:400]
    if not data.get("ok"):
        return False, data.get("description", (resp.text or "")[:400])
    return True, None


def _default_subscribers():
    return {"chat_ids": [], "update_offset": 0}


def load_subscribers():
    data = _default_subscribers()
    if WORKER_SUBSCRIBERS_URL:
        try:
            headers = {}
            if WORKER_API_SECRET:
                headers["Authorization"] = f"Bearer {WORKER_API_SECRET}"
            resp = requests.get(WORKER_SUBSCRIBERS_URL, headers=headers, timeout=15)
            if resp.ok:
                loaded = _response_json(resp, default={})
                if isinstance(loaded.get("chat_ids"), list):
                    data["chat_ids"] = [str(c) for c in loaded["chat_ids"]]
                    log(f"📬 Abonnés chargés depuis Worker : {len(data['chat_ids'])}")
                    return data
            log(f"⚠️ Worker subscribers HTTP {resp.status_code}")
        except Exception as e:
            log(f"⚠️ Worker subscribers : {e}")
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


def _handle_menu_action(chat_id, action_key, skip_pending_msg=False):
    log(f"📲 Commande '{action_key}' demandée par {chat_id}")
    if not skip_pending_msg:
        send_to_chat(chat_id, "⏳ <i>Analyse en cours…</i>")
    try:
        scan = get_scan_data(force=False)
        formatters = {
            "highlights": lambda: format_highlights_telegram(
                scan["stock_highlights"], scan["etf_highlights"], scan["spy_ret_20d"], scan["options_map"],
            ),
            "actions": lambda: format_actions_telegram(
                scan["df_momentum"], scan["options_map"], scan["spy_ret_20d"],
                highlight_tickers=[r["Ticker"] for r in scan["stock_highlights"]],
            ),
            "etfs": lambda: format_etfs_telegram(
                scan["etf_data"], scan["spy_ret_20d"], etf_highlights=scan["etf_highlights"],
            ),
            "runners": lambda: format_runners_telegram(
                scan["df_runners"], scan["options_map"], scan["spy_ret_20d"],
                stock_highlights=scan["stock_highlights"],
            ),
            "furtifs": lambda: format_furtifs_telegram(
                scan["df_stealth"], scan["options_map"], scan["spy_ret_20d"],
                df_stealth_pool=scan.get("df_stealth_pool"),
            ),
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
        updates = _response_json(resp, default={}).get("result", [])
    except Exception as e:
        log(f"⚠️ getUpdates : {e}")
        return data

    if not updates:
        log("📭 Aucun nouveau message Telegram")
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
    if cap is not None and cap < RUNNER_MIN_MARKET_CAP:
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
        f"(cap ≥{RUNNER_MIN_MARKET_CAP // 1_000_000}M$, prix {RUNNER_MIN_PRICE}-{RUNNER_MAX_PRICE:.0f}$)"
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
            data = yf.download(chunk, period=period, progress=False, ignore_tz=True, threads=True, auto_adjust=True)
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


def risk_level(beta, short_pct, vol_30d, category=None):
    """Risque composite : beta, short interest, volatilité 30j → Faible / Moyen / Élevé."""
    points = 0
    components = 0
    is_biotech = category == "Biotech"
    if beta is not None:
        components += 1
        beta_hi = 2.2 if is_biotech else 1.8
        beta_mid = 1.5 if is_biotech else 1.2
        if beta >= beta_hi:
            points += 2
        elif beta >= beta_mid:
            points += 1
    if short_pct is not None:
        components += 1
        if short_pct >= 20:
            points += 2
        elif short_pct >= 10:
            points += 1
    if vol_30d is not None:
        components += 1
        vol_hi = 90 if is_biotech else 70
        vol_mid = 60 if is_biotech else 45
        if vol_30d >= vol_hi:
            points += 2
        elif vol_30d >= vol_mid:
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
        if dt.date() < date.today():
            return None
        return dt.strftime("%d %b")
    except (ValueError, TypeError, OSError):
        return None


def _parse_catalyst_event_date(catalyst, ref=None):
    """Extrait une date d'événement depuis un libellé catalyseur (ou None)."""
    if not catalyst:
        return None
    ref = ref or date.today()
    text = str(catalyst)

    iso = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
    if iso:
        try:
            return date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
        except ValueError:
            return None

    dmy = re.search(
        r"(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*(?:\s+(\d{4}))?",
        text,
        re.I,
    )
    if not dmy:
        return None
    year = int(dmy.group(3)) if dmy.group(3) else ref.year
    try:
        dt = datetime.strptime(f"{int(dmy.group(1))} {dmy.group(2)[:3].title()} {year}", "%d %b %Y")
        return dt.date()
    except ValueError:
        return None


def _article_date(article, ref=None):
    ts = article.get("datetime")
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(int(ts)).date()
    except (ValueError, OSError, TypeError):
        return None


def future_catalyst_only(catalyst, ref=None, source_date=None):
    """Garde le catalyseur seulement s'il est à venir ou récent (news sans date événement)."""
    if not catalyst:
        return None
    text = str(catalyst).strip()
    if not text:
        return None
    ref = ref or date.today()
    event_date = _parse_catalyst_event_date(text, ref)
    if event_date is None:
        if source_date is not None and (ref - source_date).days > NEWS_CATALYST_MAX_AGE_DAYS:
            return None
        return text
    if event_date >= ref:
        return text
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


def _empty_analyst_entry():
    return {
        "recommendation": "", "is_buy": False, "target": None,
        "upside_pct": None, "market_cap": None, "sector": "", "industry": "", "category": "Autre",
        "beta": None, "short_pct": None, "earnings_date": None,
    }


def _fetch_one_analyst(ticker):
    entry = _empty_analyst_entry()
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
    return entry


def fetch_analyst_meta(tickers):
    log(f"📊 Métadonnées Yahoo ({len(tickers)} tickers)...")
    meta = {}
    to_fetch = []
    yahoo_cache_dir = os.path.join(CACHE_DIR, "yahoo")
    for ticker in tickers:
        cache_path = os.path.join(yahoo_cache_dir, f"{ticker}.pkl")
        cached = _load_pickle_cache(cache_path, META_CACHE_TTL_SEC)
        if cached:
            meta[ticker] = cached
        else:
            to_fetch.append(ticker)

    if to_fetch:
        workers = min(YAHOO_META_WORKERS, len(to_fetch))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(_fetch_one_analyst, t): t for t in to_fetch}
            for fut in as_completed(futures):
                ticker = futures[fut]
                try:
                    entry = fut.result()
                except Exception:
                    entry = _empty_analyst_entry()
                meta[ticker] = entry
                _save_pickle_cache(os.path.join(yahoo_cache_dir, f"{ticker}.pkl"), entry)

    log(f"   {sum(1 for m in meta.values() if m['is_buy'])} Buy (Yahoo)")
    return meta


def _empty_finnhub_entry():
    return {
        "rec_buy_pct": None, "news_score": None, "news_pct": None,
        "bullish_pct": None, "bearish_pct": None,
        "social_score": None, "social_pct": None,
        "price_target": None, "price_target_upside": None,
        "negative_fda_news": False, "catalyst": None, "catalyst_article_date": None,
        "finnhub_ok": False,
    }


def _fetch_one_finnhub_meta(ticker, today, news_from, social_from, news_to, social_to):
    entry = _empty_finnhub_entry()
    params = {"symbol": ticker, "token": FINNHUB_API_KEY}
    try:
        rec_resp = _http_get(f"{FINNHUB_BASE}/stock/recommendation", params=params)
        if rec_resp:
            rec_data = _response_json(rec_resp, default=[])
            if isinstance(rec_data, list) and rec_data:
                latest = rec_data[0]
                buy = int(latest.get("buy", 0) or 0) + int(latest.get("strongBuy", 0) or 0)
                total = buy + int(latest.get("hold", 0) or 0) + int(latest.get("sell", 0) or 0) + int(latest.get("strongSell", 0) or 0)
                if total > 0:
                    entry["rec_buy_pct"] = round(100 * buy / total, 1)

        news_resp = _http_get(f"{FINNHUB_BASE}/news-sentiment", params=params)
        if news_resp:
            news = _response_json(news_resp, default={}) or {}
            ns = news.get("companyNewsScore")
            entry["news_score"] = ns
            if ns is not None:
                entry["news_pct"] = round(float(ns) * 100, 0)
            sent = news.get("sentiment") or {}
            entry["bullish_pct"] = sent.get("bullishPercent")
            entry["bearish_pct"] = sent.get("bearishPercent")

        social_resp = _http_get(
            f"{FINNHUB_BASE}/stock/social-sentiment",
            params={**params, "from": social_from, "to": social_to},
        )
        if social_resp:
            social_data = _response_json(social_resp, default=[])
            items = social_data if isinstance(social_data, list) else social_data.get("data", [])
            if items:
                scores = [float(x.get("score", 0) or 0) for x in items if x.get("score") is not None]
                if scores:
                    avg = sum(scores) / len(scores)
                    entry["social_score"] = round(avg, 3)
                    entry["social_pct"] = round(min(max((avg + 1) / 2 * 100, 0), 100), 0)

        pt_resp = _http_get(f"{FINNHUB_BASE}/stock/price-target", params=params)
        if pt_resp:
            pt = _response_json(pt_resp, default={}) or {}
            target = pt.get("targetMean") or pt.get("targetHigh")
            if target:
                entry["price_target"] = float(target)

        cn_resp = _http_get(
            f"{FINNHUB_BASE}/company-news",
            params={**params, "from": news_from, "to": news_to},
        )
        if cn_resp:
            for article in _response_json(cn_resp, default=[]) or []:
                art_date = _article_date(article, today)
                if art_date and (today - art_date).days > NEWS_CATALYST_MAX_AGE_DAYS:
                    continue
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
                        entry["catalyst_article_date"] = art_date
                        if cat == "Résultats / guidance" and headline:
                            entry["catalyst"] = f"{cat} — {headline[:60]}"

        earn_resp = _http_get(
            f"{FINNHUB_BASE}/calendar/earnings",
            params={**params, "from": today.isoformat(), "to": (today + timedelta(days=120)).isoformat()},
        )
        if earn_resp and not entry.get("catalyst"):
            for ev in _response_json(earn_resp, default=[]) or []:
                if (ev.get("symbol") or "").upper() != ticker:
                    continue
                ed = ev.get("date") or ev.get("period")
                if not ed:
                    entry["catalyst"] = "Résultats à venir"
                    break
                try:
                    ed_date = datetime.strptime(str(ed)[:10], "%Y-%m-%d").date()
                except ValueError:
                    ed_date = None
                if ed_date is None or ed_date >= today:
                    entry["catalyst"] = f"Résultats le {ed}"
                    break
        entry["catalyst"] = future_catalyst_only(
            entry.get("catalyst"), today, entry.get("catalyst_article_date"),
        )
        entry["finnhub_ok"] = True
    except Exception as e:
        log(f"   ⚠️ Finnhub {ticker}: {e}")
    return ticker, entry


def fetch_finnhub_meta(tickers):
    """Finnhub — finalistes : reco, news, social, price target, alertes FDA."""
    if not FINNHUB_API_KEY:
        log("📊 Finnhub : clé absente — sentiment ignoré")
        return {t: dict(_empty_finnhub_entry()) for t in tickers}

    log(f"📊 Finnhub sentiment ({len(tickers)} finalistes)...")
    today = datetime.now().date()
    news_from = (today - timedelta(days=NEWS_LOOKBACK_DAYS)).isoformat()
    social_from = (today - timedelta(days=7)).isoformat()
    news_to = social_to = today.isoformat()

    meta = {}
    fh_cache_dir = os.path.join(CACHE_DIR, "finnhub")
    to_fetch = []
    for ticker in tickers:
        cache_path = os.path.join(fh_cache_dir, f"{ticker}.pkl")
        cached = _load_pickle_cache(cache_path, META_CACHE_TTL_SEC)
        if cached:
            meta[ticker] = cached
        else:
            to_fetch.append(ticker)

    if to_fetch:
        workers = min(FINNHUB_WORKERS, len(to_fetch))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [
                executor.submit(
                    _fetch_one_finnhub_meta, t, today, news_from, social_from, news_to, social_to,
                )
                for t in to_fetch
            ]
            for fut in as_completed(futures):
                ticker, entry = fut.result()
                meta[ticker] = entry
                _save_pickle_cache(os.path.join(fh_cache_dir, f"{ticker}.pkl"), entry)

    ok = sum(1 for m in meta.values() if m["finnhub_ok"])
    log(f"   Finnhub OK : {ok}/{len(tickers)}")
    return meta


def _passes_market_cap(analyst):
    cap = analyst.get("market_cap")
    return cap is not None and cap >= RUNNER_MIN_MARKET_CAP


def _furtif_signal_quality(var_1d, var_20d):
    """Furtif : volume spot discret — pas besoin d'un gros jour vert."""
    try:
        if var_1d is not None and float(var_1d) <= STEALTH_MIN_VAR1D:
            return False
        if var_20d is not None and float(var_20d) >= WHALE_MAX_VAR20:
            return False
    except (TypeError, ValueError):
        return False
    return True


def _whale_signal_quality(var_1d, var_20d):
    """CALL OTM crédible : jour vert et pas déjà +45 % sur 20j."""
    try:
        if var_1d is None or float(var_1d) <= WHALE_MIN_VAR1D:
            return False
        if var_20d is not None and float(var_20d) >= WHALE_MAX_VAR20:
            return False
    except (TypeError, ValueError):
        return False
    return True


def _effective_whale_vol_oi(row, options_map=None, top_vol_oi=None, stealth=False):
    """Vol/OI options — critères assouplis pour les furtifs."""
    vol_oi = top_vol_oi
    ticker = row.get("Ticker") if isinstance(row, dict) else row["Ticker"]
    if vol_oi is None and options_map:
        contracts = options_map.get(ticker)
        if contracts:
            vol_oi = contracts[0].get("vol_oi")
    if vol_oi is None:
        return None
    var_1d = row.get("Var1j") if isinstance(row, dict) else row["Var1j"]
    var_20d = row.get("Var20j") if isinstance(row, dict) else row["Var20j"]
    check = _furtif_signal_quality if stealth else _whale_signal_quality
    if check(var_1d, var_20d):
        return vol_oi
    return None


def _format_analyst_parts(row):
    """FH (consensus analystes Finnhub) + Target (upside prix cible) — complémentaires."""
    parts = []
    rb = row.get("RecBuyPct")
    up = row.get("TargetUpside")
    if _is_valid_num(rb):
        parts.append(f"FH {float(rb):.0f} %")
    if _is_valid_num(up):
        tgt = _format_target_upside(up)
        if tgt:
            parts.append(tgt)
    elif row.get("AnalystBuy") and not parts:
        parts.append("Avis analystes : achat")
    return parts


def _format_target_upside(up):
    pct = _fmt_pct(up, decimals=0, signed=True)
    return f"Objectif analystes {pct}" if pct else None


def _passes_runner_highlight_slot(row):
    """Slots runners Highlights : pas de target analyste négatif ou trop faible."""
    up = row.get("TargetUpside")
    if up is None or (isinstance(up, float) and pd.isna(up)):
        return True
    return float(up) >= HIGHLIGHT_RUNNER_MIN_TARGET_UPSIDE


def _passes_highlight_gate(row):
    """Filtres durs pour le top Highlights."""
    rb = row.get("RecBuyPct")
    if rb is not None and not pd.isna(rb) and float(rb) < HIGHLIGHT_MIN_FH_PCT:
        return False
    if _safe_num(row.get("Var20j"), 0) > HIGHLIGHT_MAX_VAR20_HIGHLIGHTS:
        return False
    return True


def filter_quality_df(df, early_entry_sort=False):
    """Filtres qualité menus — gate Highlights + target min."""
    if df is None or df.empty:
        return df
    rows = [
        row.to_dict() for _, row in df.iterrows()
        if _passes_highlight_gate(row.to_dict()) and _passes_runner_highlight_slot(row.to_dict())
    ]
    if not rows:
        return pd.DataFrame()
    if early_entry_sort:
        rows = sorted(rows, key=_highlight_rank_score, reverse=True)
        return pd.DataFrame(rows)
    out = pd.DataFrame(rows)
    return out.sort_values(by=["Score", "Var20j"], ascending=False)


def _highlight_rank_score(row):
    """Score de tri Highlights — pénalise les actions déjà parties."""
    score = float(_safe_num(row.get("Score"), 0))
    var20 = _safe_num(row.get("Var20j"), 0)
    if var20 > HIGHLIGHT_SWEET_VAR20_MAX:
        score -= min((var20 - HIGHLIGHT_SWEET_VAR20_MAX) * 1.2, 45)
    return score


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
    if var_20d is not None and var_20d > HIGHLIGHT_SWEET_VAR20_MAX:
        score -= min((var_20d - HIGHLIGHT_SWEET_VAR20_MAX) * 1.5, 35)
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


def options_score(top_vol_oi, var_1d=None, var_20d=None):
    if not top_vol_oi or not _whale_signal_quality(var_1d, var_20d):
        return 0
    if top_vol_oi >= 15:
        return 100
    if top_vol_oi >= 10:
        return 85
    if top_vol_oi >= STEALTH_MIN_OPT_VOL_OI:
        return round(min(50 + (top_vol_oi - STEALTH_MIN_OPT_VOL_OI) * 7, 80), 0)
    return round(min(top_vol_oi * 10, 45), 0)


def global_score(momentum, analyst, options, sentiment):
    m = _score_component(momentum)
    a = _score_component(analyst)
    o = _score_component(options)
    s = float(sentiment) if _is_valid_num(sentiment) else 50.0
    s = max(0.0, min(100.0, s))
    return round(
        SCORE_W_MOMENTUM * m
        + SCORE_W_ANALYST * a
        + SCORE_W_OPTIONS * o
        + SCORE_W_SENTIMENT * s,
        0,
    )


def compute_scores(var_1d, var_5d, var_20d, rs_20d, vol_ratio, is_shorted, accelerating,
                   is_buy, target_upside, price, finnhub, top_vol_oi=None):
    accelerating = accelerating or (
        var_5d is not None and var_20d is not None and var_5d > 0 and var_5d > (var_20d / 4)
    )
    m = momentum_score(var_1d, var_5d, var_20d, rs_20d, vol_ratio, is_shorted, accelerating)
    a = analyst_score(is_buy, target_upside, price, finnhub)
    o = options_score(top_vol_oi, var_1d, var_20d)
    s = sentiment_score(finnhub)
    g = global_score(m, a, o, s)
    if not _is_valid_num(g):
        g = global_score(_score_component(m), _score_component(a), _score_component(o), s)
    return m, a, o, s, g


def _row_is_accelerating(row):
    var_5d, var_20d = row.get("Var5j"), row.get("Var20j")
    if not _is_valid_num(var_5d) or not _is_valid_num(var_20d):
        return False
    return float(var_5d) > 0 and float(var_5d) > float(var_20d) / 4


def _recompute_row_scores(row, meta=None, options_map=None, stealth=None):
    """Recalcule Score et sous-scores à partir des données de la ligne."""
    r = dict(row)
    meta = meta if meta is not None else _finnhub_meta_from_row(r)
    if stealth is None:
        stealth = r.get("Signal") == "furtif"
    target_upside = _effective_target_upside(r.get("Prix"), r.get("TargetUpside"), meta)
    top_vol_oi = _effective_whale_vol_oi(
        r, options_map, top_vol_oi=r.get("TopVolOI"), stealth=stealth,
    )
    m, a, o, s, g = compute_scores(
        r.get("Var1j"), r.get("Var5j"), r.get("Var20j"), r.get("RS20j"), r.get("VolRatio"),
        r.get("Shorted"), _row_is_accelerating(r),
        r.get("AnalystBuy"), target_upside, r.get("Prix"), meta, top_vol_oi,
    )
    r.update({
        "MomentumScore": m, "AnalystScore": a, "OptionsScore": o,
        "SentimentScore": s, "Score": g,
        "TargetUpside": target_upside,
        "TopVolOI": top_vol_oi,
    })
    return r


def _display_score(row):
    score = row.get("Score")
    if not _is_valid_num(score):
        score = global_score(
            row.get("MomentumScore"), row.get("AnalystScore"),
            row.get("OptionsScore"), row.get("SentimentScore"),
        )
    if not _is_valid_num(score):
        return "n/d"
    return str(int(round(float(score))))


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
    category = analyst.get("category", "Autre")
    risk_label, risk_emoji = risk_level(beta, short_pct, vol_30d, category)
    catalyst = future_catalyst_only(fh.get("catalyst"))
    if not catalyst and analyst.get("earnings_date"):
        catalyst = future_catalyst_only(f"Résultats le {analyst['earnings_date']}")
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


def _fda_event_symbols(ev):
    """Extrait les tickers d'un événement FDA (champ symbol ou texte)."""
    symbols = []
    for key in ("symbol", "ticker", "Symbol", "Ticker"):
        sym = (ev.get(key) or "").strip().upper()
        if sym and SYMBOL_RE.match(sym):
            symbols.append(sym)
    text = " ".join(
        str(ev.get(k) or "") for k in ("eventDescription", "description", "event", "title")
    )
    for match in re.findall(r"\(([A-Z]{1,5})\)", text):
        if SYMBOL_RE.match(match):
            symbols.append(match)
    return symbols


def fetch_fda_calendar_tickers(nasdaq_stocks):
    """Symboles US liés à des événements FDA à venir (univers pré-catalyseur biotech)."""
    if not FINNHUB_API_KEY:
        return []
    try:
        resp = _http_get(
            f"{FINNHUB_BASE}/fda-advisory-committee-calendar",
            params={"token": FINNHUB_API_KEY},
            timeout=15,
        )
        if not resp:
            return []
        events = _response_json(resp, default=[])
        if not isinstance(events, list):
            return []
        today = date.today()
        symbols = []
        for ev in events:
            if not isinstance(ev, dict):
                continue
            event_date = None
            for key in ("fromDate", "toDate", "date"):
                raw = ev.get(key)
                if not raw:
                    continue
                try:
                    event_date = datetime.strptime(str(raw)[:10], "%Y-%m-%d").date()
                    break
                except ValueError:
                    continue
            if event_date is not None and event_date < today:
                continue
            for sym in _fda_event_symbols(ev):
                if sym in nasdaq_stocks:
                    symbols.append(sym)
        symbols = list(dict.fromkeys(symbols))
        if symbols:
            log(f"🧬 FDA calendar : {len(symbols)} symboles pré-catalyseur")
        return symbols
    except Exception as e:
        log(f"   ⚠️ FDA calendar : {e}")
        return []


def analyze_candidates(tickers, sources, spy_ret_20d, precatalyst_tickers=None):
    log(f"📡 Analyse momentum {len(tickers)} tickers...")
    precatalyst_set = set(precatalyst_tickers or [])
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
            if (
                ticker in precatalyst_set
                and analyst.get("category") == "Biotech"
                and (var_20d is None or var_20d <= BIOTECH_PRE_CATALYST_MAX_VAR20)
                and price >= RUNNER_MIN_PRICE
            ):
                momentum_pool.append(_build_row(
                    ticker, price, var_1d, var_5d, var_20d or 0, rs_20d or 0,
                    vol_ratio, analyst, sources, {}, vol_30d,
                ))
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
            r = _recompute_row_scores(r, meta=meta)
            catalyst = future_catalyst_only(
                meta.get("catalyst") or r.get("Catalyst"),
                source_date=meta.get("catalyst_article_date"),
            )
            r.update({
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
        if _row_qualifies_x2(row.to_dict()):
            rows.append(row.to_dict())
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    return df.sort_values(by=["TargetUpside", "AnalystScore", "Score"], ascending=False)


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
        rows.append(_recompute_row_scores(row.to_dict(), options_map=options_map))
    out = pd.DataFrame(rows)
    return out.sort_values(by=["Score", "Var20j"], ascending=False) if not out.empty else out


def short_expiry(exp_str):
    try:
        return datetime.strptime(exp_str, "%Y-%m-%d").strftime("%b-%y")
    except ValueError:
        return exp_str


def _uoa_calls_otm(df, expiry, spot_price, min_volume, min_vol_oi=None, otm_min_pct=None, min_oi=None):
    if df is None or df.empty or spot_price is None:
        return []
    min_vol_oi = OPTIONS_MIN_VOL_OI if min_vol_oi is None else min_vol_oi
    otm_min_pct = OPTIONS_OTM_MIN_PCT if otm_min_pct is None else otm_min_pct
    min_oi = OPTIONS_MIN_OI if min_oi is None else min_oi
    min_strike = spot_price * otm_min_pct
    hits = []
    for row in df.itertuples(index=False):
        vol, oi, strike = getattr(row, "volume", None), getattr(row, "openInterest", None), getattr(row, "strike", None)
        if vol is None or oi is None or strike is None or pd.isna(vol) or pd.isna(oi) or pd.isna(strike):
            continue
        strike = float(strike)
        if strike < min_strike:
            continue
        vol, oi = int(vol), int(oi)
        if vol < min_volume or oi < min_oi or vol <= oi:
            continue
        vol_oi = vol / oi
        if vol_oi < min_vol_oi:
            continue
        hits.append({
            "side": "CALL", "strike": strike, "expiry": expiry,
            "volume": vol, "oi": oi, "vol_oi": round(vol_oi, 1),
            "otm_pct": round((strike / spot_price - 1) * 100, 1),
        })
    return hits


def scan_ticker_options(ticker, spot_price, stealth=False):
    try:
        tk = yf.Ticker(to_yahoo_symbol(ticker))
        expirations = tk.options
        if not expirations:
            return []
    except Exception:
        return []
    min_volume = STEALTH_OPTIONS_MIN_VOLUME if stealth else OPTIONS_MIN_VOLUME_STOCK
    min_vol_oi = STEALTH_OPTIONS_MIN_VOL_OI if stealth else OPTIONS_MIN_VOL_OI
    otm_min = STEALTH_OPTIONS_OTM_MIN_PCT if stealth else OPTIONS_OTM_MIN_PCT
    min_oi = STEALTH_OPTIONS_MIN_OI if stealth else OPTIONS_MIN_OI
    hits = []
    for exp in expirations[:OPTIONS_MAX_EXPIRATIONS]:
        try:
            chain = tk.option_chain(exp)
            hits.extend(_uoa_calls_otm(
                chain.calls, exp, spot_price, min_volume,
                min_vol_oi=min_vol_oi, otm_min_pct=otm_min, min_oi=min_oi,
            ))
        except Exception:
            pass
        time.sleep(OPTIONS_DELAY_SEC)
    hits.sort(key=lambda x: x["vol_oi"], reverse=True)
    return hits[:OPTIONS_CONTRACTS_PER_TICKER]


def scan_options_for_df(df, label, stealth=False):
    if df.empty:
        return {}
    log(f"🐳 Options {label} ({len(df)} tickers)...")
    options_map = {}
    for _, row in df.iterrows():
        contracts = scan_ticker_options(row["Ticker"], row["Prix"], stealth=stealth)
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
        if not _furtif_signal_quality(row["Var1j"], row["Var20j"]):
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
    """Top actions — slots x2 dédiés + runners early-entry."""
    x2_best = {}
    for signal, df in (("x2", df_x2), ("runner", df_runners), ("furtif", df_stealth)):
        if df is None or df.empty:
            continue
        for _, row in df.iterrows():
            r = row.to_dict()
            if not _passes_highlight_gate(r) or not _row_qualifies_x2(r):
                continue
            r["Signal"] = signal
            ticker = r["Ticker"]
            up = _safe_num(r.get("TargetUpside"), 0)
            prev = x2_best.get(ticker)
            if not prev or up > _safe_num(prev.get("TargetUpside"), 0):
                x2_best[ticker] = r

    x2_picks = sorted(
        x2_best.values(),
        key=lambda r: (_safe_num(r.get("TargetUpside"), 0), _highlight_rank_score(r)),
        reverse=True,
    )[:HIGHLIGHT_X2_SLOTS]
    picked = {r["Ticker"] for r in x2_picks}

    runner_best = {}
    for signal, df in (("runner", df_runners), ("furtif", df_stealth)):
        if df is None or df.empty:
            continue
        for _, row in df.iterrows():
            r = row.to_dict()
            if not _passes_highlight_gate(r) or not _passes_runner_highlight_slot(r):
                continue
            r["Signal"] = signal
            ticker = r["Ticker"]
            if ticker in picked:
                continue
            score = _highlight_rank_score(r)
            prev = runner_best.get(ticker)
            if not prev or score > _highlight_rank_score(prev):
                runner_best[ticker] = r

    runner_picks = sorted(runner_best.values(), key=_highlight_rank_score, reverse=True)[
        :HIGHLIGHT_RUNNER_SLOTS
    ]
    return x2_picks + runner_picks


def pick_etf_highlights(etf_data):
    """Top ETF par RS 20j — max 1 par groupe thématique."""
    rows = []
    for group, items in (etf_data or {}).items():
        if not items:
            continue
        best = max(items, key=lambda x: x.get("RS20j", 0))
        r = dict(best)
        r["Group"] = group
        rows.append(r)
    rows.sort(key=lambda x: x.get("RS20j", 0), reverse=True)
    return rows[:HIGHLIGHT_MAX_ETFS]


def _format_compact_stock(row, options_map=None, stealth=False, highlight_tickers=None):
    """Une ligne action lisible — utilisée par tous les menus."""
    ticker = escape_html(row["Ticker"])
    pin = "⭐ " if highlight_tickers and row["Ticker"] in highlight_tickers else ""
    score = _display_score(row)
    risk = row.get("RiskEmoji", "🟠")
    var_s = _fmt_pct(row.get("Var20j"), 0) or "n/d"
    parts = [f"{pin}<b>{ticker}</b>", f"Score {score} {risk}", f"{var_s} sur 20 j"]
    parts.extend(_format_analyst_parts(row))
    if options_map:
        vol_oi = _effective_whale_vol_oi(row, options_map, stealth=stealth)
        if _is_valid_num(vol_oi):
            if stealth:
                parts.append(f"🕵️ gros CALL options (×{float(vol_oi):.1f})")
            else:
                parts.append(f"🐳 options actives (×{float(vol_oi):.1f})")
    catalyst = future_catalyst_only(row.get("Catalyst"))
    line = " | ".join(parts)
    if catalyst and str(catalyst).strip().lower() not in ("nan", "none", "nat"):
        line += f"\n📰 {escape_html(str(catalyst)[:55])}"
    return line + "\n"


def _spy_header(spy_ret_20d):
    if not _is_valid_num(spy_ret_20d):
        return ""
    v = float(spy_ret_20d)
    if v >= 0:
        return f"<i>Contexte : le marché US progresse de {v:.1f} % sur 20 jours.</i>\n"
    return f"<i>Contexte : le marché US recule de {abs(v):.1f} % sur 20 jours.</i>\n"


def _format_furtif_stock(row, options_map):
    return _format_compact_stock(row, options_map, stealth=True)


def _pick_sector_actions_rows(rows, highlight_tickers):
    """Épingle les Highlights du secteur puis complète avec le top score."""
    highlight_tickers = set(highlight_tickers or [])
    n_pin = sum(1 for r in rows if r["Ticker"] in highlight_tickers)
    cap = MENU_MAX_PER_SECTOR + n_pin
    picked, seen = [], set()
    for row in rows:
        if row["Ticker"] in highlight_tickers:
            picked.append(row)
            seen.add(row["Ticker"])
    for row in rows:
        if row["Ticker"] in seen:
            continue
        if len(picked) >= cap:
            break
        picked.append(row)
        seen.add(row["Ticker"])
    return picked


def format_actions_telegram(df_momentum, options_map, spy_ret_20d, highlight_tickers=None):
    message = (
        f"<b>{MENU_LABELS['actions']}</b>\n"
        f"{_spy_header(spy_ret_20d)}"
        "<i>Meilleures actions par secteur (⭐ = aussi dans Highlights).</i>\n"
    )
    highlight_tickers = set(highlight_tickers or [])
    df_momentum = filter_quality_df(df_momentum)
    if df_momentum is None or df_momentum.empty:
        return message + "<i>Aucune action qualifiée.</i>\n"
    df = df_momentum.sort_values(by=["Score", "Var20j"], ascending=False)
    grouped = {cat: [] for cat in SECTOR_ORDER}
    for _, row in df.iterrows():
        grouped.setdefault(row.get("Category", "Autre"), []).append(row)
    for cat in SECTOR_ORDER:
        rows = _pick_sector_actions_rows(grouped.get(cat, []), highlight_tickers)
        if not rows:
            continue
        message += f"\n<b>{SECTOR_LABELS[cat]}</b>\n"
        for row in rows:
            message += _format_compact_stock(row, options_map, highlight_tickers=highlight_tickers)
    return message + "\n⚠️ <i>Pas un conseil d'investissement.</i>"


def format_etfs_telegram(etf_data, spy_ret_20d, etf_highlights=None):
    message = (
        f"<b>{MENU_LABELS['etfs']}</b>\n"
        f"{_spy_header(spy_ret_20d)}"
        "<i>Meilleur fonds par thème, en hausse face au marché US sur 20 jours.</i>\n"
    )
    if not etf_data:
        return message + "<i>Données ETF indisponibles.</i>\n"
    hl_tickers = {r["Ticker"] for r in (etf_highlights or [])}
    shown = 0
    for group in ("CROISSANCE", "IA", "SEMI"):
        positive = [
            r for r in (etf_data.get(group) or [])
            if _safe_num(r.get("RS20j"), -999) > ETF_MIN_RS20
        ]
        if not positive:
            continue
        best = max(positive, key=lambda x: x.get("RS20j", 0))
        message += f"\n<b>{ETF_GROUP_LABELS[group]}</b>\n"
        message += _format_highlight_etf(best, best=best["Ticker"] in hl_tickers)
        shown += 1
    if not shown:
        return message + "<i>Aucun ETF en hausse face au marché US aujourd'hui.</i>\n"
    return message + "\n⚠️ <i>Pas un conseil d'investissement.</i>"


def format_runners_telegram(df_runners, options_map, spy_ret_20d, stock_highlights=None):
    message = (
        f"<b>{MENU_LABELS['runners']}</b>\n"
        f"{_spy_header(spy_ret_20d)}"
        "<i>Actions avec fort volume et journée positive — le mouvement est visible.</i>\n\n"
    )
    hl_tickers = {r["Ticker"] for r in (stock_highlights or [])}
    if stock_highlights:
        message += "<b>⭐ Nos sélections du jour</b>\n"
        for row in stock_highlights:
            message += _format_compact_stock(
                row, options_map, highlight_tickers=hl_tickers,
            )
        message += "\n<b>Autres runners</b>\n"
    df_runners = filter_quality_df(df_runners, early_entry_sort=True)
    if df_runners is None or df_runners.empty:
        if not stock_highlights:
            return message + "<i>Aucun runner aujourd'hui.</i>\n"
    else:
        n = 0
        for _, row in df_runners.iterrows():
            if row["Ticker"] in hl_tickers:
                continue
            if n >= MENU_MAX_RUNNERS:
                break
            message += _format_compact_stock(row, options_map)
            n += 1
        if n == 0 and not stock_highlights:
            message += "<i>Aucun runner aujourd'hui.</i>\n"
    return message + "\n⚠️ <i>Pas un conseil d'investissement.</i>"


def format_furtifs_telegram(df_stealth, options_map, spy_ret_20d, df_stealth_pool=None):
    message = (
        f"<b>🕵️ Achats furtifs</b>\n"
        f"{_spy_header(spy_ret_20d)}"
        "<i>Parieurs options actifs alors que l'action bouge peu "
        "(signe d'un pari avant un mouvement).</i>\n\n"
    )
    if df_stealth is not None and not df_stealth.empty:
        message += "<b>Signaux détectés</b>\n"
        for _, row in df_stealth.head(MENU_MAX_STEALTH).iterrows():
            message += _format_furtif_stock(row, options_map)
        return message + "\n⚠️ <i>Pas un conseil d'investissement.</i>"
    message += (
        "<i>Aucun achat furtif détecté aujourd'hui "
        "(pas d'options suspectes sur actions à volume discret).</i>\n"
    )
    return message + "\n⚠️ <i>Pas un conseil d'investissement.</i>"


def format_extended_telegram(df_extended, spy_ret_20d):
    message = (
        f"<b>📈 Déjà en forte hausse</b>\n"
        f"{_spy_header(spy_ret_20d)}"
        f"<i>Actions déjà montées de plus de {RUNNER_MAX_VAR20:.0f} % sur 20 jours "
        "— entrée tardive pour viser un doublement.</i>\n"
    )
    if df_extended is None or df_extended.empty:
        return message + "<i>Aucune action dans cette catégorie aujourd'hui.</i>\n"
    for _, row in df_extended.head(MENU_MAX_EXTENDED).iterrows():
        var_s = _fmt_pct(row.get("Var20j"), 0) or "n/d"
        prix = f"{float(row['Prix']):.0f} $" if _is_valid_num(row.get("Prix")) else "n/d"
        vol = f"×{float(row['VolRatio']):.1f}" if _is_valid_num(row.get("VolRatio")) else "n/d"
        message += (
            f"<b>{escape_html(row['Ticker'])}</b> "
            f"{var_s} sur 20 j | {prix} | volume {vol}\n"
        )
    return message + "\n⚠️ <i>Pas un conseil d'investissement.</i>"


def run_market_scan():
    """Pipeline complet — données pour tous les menus."""
    nasdaq_stocks, _ = fetch_nasdaq_trader_symbols()
    stock_quotes, sources = fetch_screener_universe()
    runner_tickers = build_runner_candidates(stock_quotes, nasdaq_stocks, sources)
    fda_tickers = fetch_fda_calendar_tickers(nasdaq_stocks)
    precatalyst_set = set(fda_tickers)
    extra = [t for t in fda_tickers if t not in runner_tickers][:BIOTECH_PRE_CATALYST_SCAN_CAP]
    tickers = runner_tickers + extra
    if not tickers:
        raise RuntimeError("Aucun candidat screener.")

    spy_ret_20d = get_spy_benchmark()
    if spy_ret_20d is not None:
        log(f"📊 Benchmark SPY 20j : {spy_ret_20d:+.2f}%")

    df_runners, df_stealth_pool, df_extended, df_momentum = analyze_candidates(
        tickers, sources, spy_ret_20d, precatalyst_tickers=precatalyst_set,
    )

    stealth_scan = df_stealth_pool.head(STEALTH_SCAN_CAP) if not df_stealth_pool.empty else df_stealth_pool
    options_map = {}
    if not df_runners.empty:
        options_map.update(scan_options_for_df(df_runners.head(OPTIONS_SCAN_MAX_TICKERS), "runners"))
    if not stealth_scan.empty:
        options_map.update(scan_options_for_df(stealth_scan, "furtif", stealth=True))

    df_stealth = build_stealth_df(stealth_scan, options_map)

    df_runners, df_stealth, df_extended, df_momentum = enrich_with_finnhub(
        df_runners, df_stealth, df_extended, df_momentum,
    )

    df_runners = apply_options_scores(df_runners, options_map)
    df_stealth = apply_options_scores(df_stealth, options_map)
    df_momentum = apply_options_scores(df_momentum, options_map)
    df_x2 = build_x2_df(df_momentum)

    stock_highlights = build_stock_highlights(df_runners, df_stealth, df_x2)
    for row in stock_highlights:
        if row["Ticker"] not in options_map:
            extra = scan_options_for_df(pd.DataFrame([row]), "highlights")
            options_map.update(extra)
    stock_highlights = [
        _recompute_row_scores(row, options_map=options_map) for row in stock_highlights
    ]

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
        "df_stealth_pool": stealth_scan,
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
        log("📦 Scan en cache mémoire (réutilisé)")
        return _scan_cache["data"]

    if not force:
        disk = _load_pickle_cache(SCAN_DISK_CACHE_FILE, SCAN_DISK_CACHE_TTL_SEC)
        if disk and isinstance(disk.get("data"), dict):
            log("📦 Scan en cache disque (réutilisé)")
            _scan_cache = {"ts": disk.get("ts", time.time()), "data": disk["data"]}
            return disk["data"]

    data = run_market_scan()
    ts = time.time()
    _scan_cache = {"ts": ts, "data": data}
    _save_pickle_cache(SCAN_DISK_CACHE_FILE, {"ts": ts, "data": data})
    return data


def _format_highlight_stock(row, options_map):
    line = _format_compact_stock(
        row, options_map,
        stealth=row.get("Signal") == "furtif",
    ).rstrip("\n")
    news = row.get("SentimentScore")
    if _is_valid_num(news) and float(news) != 50:
        extra = f" | Sentiment news {float(news):.0f}"
        if "\n📰" in line:
            line = line.replace("\n📰", extra + "\n📰", 1)
        else:
            line += extra
    if row.get("Signal") == "x2":
        line += " | 🎯 x2"
    return line + "\n"


def _format_highlight_etf(row, best=False):
    star = " ⭐" if best else ""
    var20 = _fmt_pct(row.get("Var20j"), 1) or "n/d"
    vs_marche = _fmt_pct(row.get("RS20j"), 1) or "n/d"
    prix = f"{float(row.get('Prix')):.0f} $" if _is_valid_num(row.get("Prix")) else "n/d"
    return (
        f"<b>{escape_html(row['Ticker'])}</b>{star} "
        f"{var20} sur 20 j | {vs_marche} vs marché US | {prix}\n"
    )


def format_highlights_telegram(stock_highlights, etf_highlights, spy_ret_20d, options_map):
    message = (
        "🚀 <b>AnisTrade — Highlights</b>\n"
        f"{_spy_header(spy_ret_20d)}"
        "<i>Notre sélection du jour : actions et ETF les plus prometteurs.</i>\n\n"
        "<b>📈 Meilleures actions</b>\n"
    )
    if stock_highlights:
        for row in stock_highlights:
            message += _format_highlight_stock(row, options_map)
    else:
        message += "<i>Rien de convaincant aujourd'hui.</i>\n"

    message += "\n<b>📊 Fonds ETF</b>\n"
    if etf_highlights:
        for i, row in enumerate(etf_highlights):
            message += _format_highlight_etf(row, best=(i == 0))
    else:
        message += "<i>Aucun fonds ETF à mettre en avant.</i>\n"

    message += "\n⚠️ <i>Pas un conseil d'investissement.</i>"
    return message


def main():
    if not TELEGRAM_TOKEN:
        raise Exception("Secret TELEGRAM_TOKEN manquant. Vérifiez vos Secrets GitHub.")

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


def run_command_for_chat(action_key, chat_id):
    """Exécute une commande menu pour un seul chat (déclenché par GitHub Actions)."""
    if action_key not in {
        "highlights", "actions", "etfs", "runners", "furtifs", "extended",
    }:
        raise ValueError(f"Commande inconnue : {action_key}")
    # Le Worker a déjà envoyé « Analyse en cours »
    _handle_menu_action(chat_id, action_key, skip_pending_msg=True)


def run_bot_polling():
    if not TELEGRAM_TOKEN:
        raise SystemExit("TELEGRAM_TOKEN manquant")
    register_bot_commands()
    log("🤖 Bot AnisTrade — commandes / (Ctrl+C pour arrêter)")
    while True:
        process_telegram_updates(handle_menus=True)
        time.sleep(2)


def run_poll_window(seconds=270):
    """Écoute Telegram en boucle (pour GitHub Actions, ~4m30 toutes les 5 min)."""
    if not TELEGRAM_TOKEN:
        raise SystemExit("TELEGRAM_TOKEN manquant")
    register_bot_commands()
    data = load_subscribers()
    log(f"📡 Fenêtre poll {seconds}s (offset={data.get('update_offset', 0)})…")
    deadline = time.time() + seconds
    while time.time() < deadline:
        data = process_telegram_updates(handle_menus=True)
        time.sleep(2)
    log(f"📬 Fin fenêtre — abonnés : {len(data.get('chat_ids', []))} (offset={data.get('update_offset', 0)})")


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
    elif len(sys.argv) > 1 and sys.argv[1] == "--poll-window":
        seconds = int(sys.argv[2]) if len(sys.argv) > 2 else 270
        run_poll_window(seconds)
    elif len(sys.argv) > 1 and sys.argv[1] == "--command":
        action = sys.argv[sys.argv.index("--command") + 1]
        chat_id = sys.argv[sys.argv.index("--chat-id") + 1]
        if not TELEGRAM_TOKEN:
            raise SystemExit("TELEGRAM_TOKEN manquant")
        run_command_for_chat(action, chat_id)
    else:
        main()
