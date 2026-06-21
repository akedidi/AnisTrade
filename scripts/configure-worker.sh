#!/usr/bin/env bash
# Configure Cloudflare Worker secrets (requires wrangler login locally).
set -euo pipefail
cd "$(dirname "$0")/../worker"

: "${TELEGRAM_TOKEN:?TELEGRAM_TOKEN requis}"
: "${GITHUB_PAT:?GITHUB_PAT requis (PAT avec actions:write)}"

WEBHOOK_SECRET="${WEBHOOK_SECRET:-$(openssl rand -hex 32)}"
SETUP_SECRET="${SETUP_SECRET:-$(openssl rand -hex 32)}"
WORKER_API_SECRET="${WORKER_API_SECRET:-$(openssl rand -hex 32)}"
GITHUB_REPO="${GITHUB_REPO:-akedidi/AnisTrade}"
WORKER_URL="${WORKER_URL:-https://anistrade-telegram.kedidi-anis.workers.dev}"

echo "→ Secrets Worker…"
printf '%s' "$TELEGRAM_TOKEN" | npx wrangler secret put TELEGRAM_TOKEN
printf '%s' "$GITHUB_PAT" | npx wrangler secret put GITHUB_PAT
printf '%s' "$GITHUB_REPO" | npx wrangler secret put GITHUB_REPO
printf '%s' "$WEBHOOK_SECRET" | npx wrangler secret put WEBHOOK_SECRET
printf '%s' "$SETUP_SECRET" | npx wrangler secret put SETUP_SECRET
printf '%s' "$WORKER_API_SECRET" | npx wrangler secret put WORKER_API_SECRET

echo "→ Webhook Telegram…"
curl -fsS "${WORKER_URL}/setup?secret=${SETUP_SECRET}&import=8086813061,5404451034,-1004457117208"
echo ""

echo "→ Secrets GitHub…"
GH_TOKEN="$GITHUB_PAT" gh secret set WORKER_API_SECRET -R "$GITHUB_REPO" --body "$WORKER_API_SECRET"
GH_TOKEN="$GITHUB_PAT" gh secret set WORKER_SUBSCRIBERS_URL -R "$GITHUB_REPO" --body "${WORKER_URL}/api/subscribers"

echo "✅ Worker configuré : $WORKER_URL"
echo "   SETUP_SECRET=$SETUP_SECRET (garde-le pour re-setup)"
