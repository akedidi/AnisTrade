# AnisTrade — Cloudflare Worker + GitHub Actions

**Worker déployé** : `https://anistrade-telegram.kedidi-anis.workers.dev`

| Composant | Rôle |
|-----------|------|
| **Cloudflare Worker** | Webhook Telegram instantané |
| **GitHub Actions** | Scans lourds (`telegram-command.yml`) + Highlights 14h UTC |

---

## Architecture

- Compte [Cloudflare](https://dash.cloudflare.com) (gratuit)
- Node.js 18+
- Token Telegram (BotFather)
- Personal Access Token GitHub avec scope `repo` + `actions:write`

---

## 2. Créer le KV (abonnés)

```bash
cd worker
npm install
npx wrangler kv namespace create SUBSCRIBERS
```

Copie l'`id` retourné dans `worker/wrangler.toml` → `[[kv_namespaces]]` → `id = "..."`.

---

## 3. Secrets Cloudflare

```bash
cd worker

# Token du bot Telegram
npx wrangler secret put TELEGRAM_TOKEN

# PAT GitHub (actions:write) — déclenche les scans
npx wrangler secret put GITHUB_PAT

# Repo au format owner/name
npx wrangler secret put GITHUB_REPO
# ex: akedidi/AnisTrade

# Secret aléatoire pour le webhook Telegram (openssl rand -hex 32)
npx wrangler secret put WEBHOOK_SECRET

# Secret pour l'endpoint /setup (openssl rand -hex 32)
npx wrangler secret put SETUP_SECRET

# Secret pour l'API /api/subscribers (utilisé par GitHub Actions)
npx wrangler secret put WORKER_API_SECRET
```

---

## 4. Déployer le Worker

```bash
cd worker
npx wrangler deploy
```

Note l'URL affichée, ex: `https://anistrade-telegram.<account>.workers.dev`

---

## 5. Activer le webhook Telegram

Remplace `SETUP_SECRET` et importe tes abonnés existants :

```bash
curl "https://anistrade-telegram.<account>.workers.dev/setup?secret=TON_SETUP_SECRET&import=8086813061,5404451034"
```

Réponse attendue : `webhook.ok: true`, liste des commandes enregistrées.

---

## 6. Secrets GitHub (repo → Settings → Secrets)

| Secret | Valeur |
|--------|--------|
| `TELEGRAM_TOKEN` | Token BotFather (déjà présent) |
| `FINNHUB_API_KEY` | Clé Finnhub (déjà présent) |
| `WORKER_SUBSCRIBERS_URL` | `https://anistrade-telegram.<account>.workers.dev/api/subscribers` |
| `WORKER_API_SECRET` | Même valeur que sur Cloudflare |

`TELEGRAM_CHAT_ID` reste optionnel (rétrocompatibilité).

---

## 7. Tester

1. Envoie `/start` au bot → réponse **< 1 seconde**
2. Envoie `/highlights` → « ⏳ Analyse en cours… » **immédiat**, résultat après le scan GH (~4–8 min)
3. Vérifie les runs : GitHub → Actions → **Telegram Command Scan**

---

## Endpoints Worker

| Route | Description |
|-------|-------------|
| `POST /webhook` | Webhook Telegram (automatique) |
| `GET /api/subscribers` | Liste des `chat_id` (Bearer `WORKER_API_SECRET`) |
| `GET /setup?secret=…` | Configure webhook + commandes bot |

---

## Mode local (dev / secours)

```bash
export TELEGRAM_TOKEN="..."
python bot.py --bot
```

---

## Dépannage

**Le bot ne répond pas**
- Vérifie que le webhook est actif : `curl https://api.telegram.org/bot<TOKEN>/getWebhookInfo`
- L'URL doit pointer vers ton Worker, pas un ancien poll

**« Impossible de lancer l'analyse »**
- Vérifie `GITHUB_PAT` et `GITHUB_REPO` sur Cloudflare
- Le PAT doit avoir accès au repo et `actions:write`

**Alerte Highlights 14h sans destinataire**
- Vérifie `WORKER_SUBSCRIBERS_URL` et `WORKER_API_SECRET` dans les secrets GitHub
- Au moins un `/start` doit avoir été envoyé après déploiement du Worker
