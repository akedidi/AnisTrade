export interface Env {
  TELEGRAM_TOKEN: string;
  GITHUB_PAT: string;
  GITHUB_REPO: string;
  WEBHOOK_SECRET: string;
  SETUP_SECRET: string;
  WORKER_API_SECRET: string;
  SUBSCRIBERS: KVNamespace;
}

const KV_KEY = "chat_ids";

const SCAN_COMMANDS: Record<string, string> = {
  "/highlights": "highlights",
  "/actions": "actions",
  "/etfs": "etfs",
  "/runners": "runners",
  "/furtifs": "furtifs",
  "/dejarenrun": "extended",
  "/extended": "extended",
};

const BOT_COMMANDS = [
  { command: "start", description: "S'abonner aux alertes Highlights" },
  { command: "highlights", description: "Top actions + ETF" },
  { command: "actions", description: "Actions par secteur" },
  { command: "etfs", description: "ETF par catégorie" },
  { command: "runners", description: "Runners (vol + momentum)" },
  { command: "furtifs", description: "Achats furtifs (options)" },
  { command: "dejarenrun", description: "Déjà en run (>60% 20j)" },
  { command: "menu", description: "Afficher les commandes" },
];

const MENU_HELP =
  "📋 <b>Commandes AnisTrade</b>\n" +
  "Tapez <b>/</b> ou ouvrez le menu du bot :\n\n" +
  "/highlights — top actions + ETF\n" +
  "/actions — actions par secteur\n" +
  "/etfs — ETF par catégorie\n" +
  "/runners — momentum + volume\n" +
  "/furtifs — achats furtifs\n" +
  "/dejarenrun — déjà en run";

function welcomeMessage(chatId: string): string {
  return (
    "✅ <b>AnisTrade</b> — abonnement confirmé.\n" +
    `🆔 Votre <b>chat_id</b> : <code>${chatId}</code>\n\n` +
    "Ouvrez le menu du bot (bouton <b>/</b>) pour explorer les signaux.\n" +
    "Les <b>Highlights</b> automatiques sont envoyés à chaque alerte planifiée."
  );
}

async function getChatIds(env: Env): Promise<string[]> {
  const raw = await env.SUBSCRIBERS.get(KV_KEY);
  if (!raw) return [];
  try {
    const ids = JSON.parse(raw);
    return Array.isArray(ids) ? ids.map(String) : [];
  } catch {
    return [];
  }
}

async function saveChatIds(env: Env, chatIds: string[]): Promise<void> {
  await env.SUBSCRIBERS.put(KV_KEY, JSON.stringify(chatIds));
}

async function addSubscriber(env: Env, chatId: string): Promise<boolean> {
  const ids = await getChatIds(env);
  if (ids.includes(chatId)) return false;
  ids.push(chatId);
  await saveChatIds(env, ids);
  return true;
}

async function telegramApi(env: Env, method: string, body: Record<string, unknown>): Promise<boolean> {
  const res = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_TOKEN}/${method}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = (await res.json()) as { ok?: boolean; description?: string };
  if (!data.ok) {
    console.error(`Telegram ${method}:`, data.description);
    return false;
  }
  return true;
}

async function sendMessage(env: Env, chatId: string, text: string): Promise<void> {
  await telegramApi(env, "sendMessage", {
    chat_id: chatId,
    text,
    parse_mode: "HTML",
    disable_web_page_preview: true,
  });
}

async function triggerGitHubScan(env: Env, command: string, chatId: string): Promise<void> {
  const res = await fetch(
    `https://api.github.com/repos/${env.GITHUB_REPO}/actions/workflows/telegram-command.yml/dispatches`,
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${env.GITHUB_PAT}`,
        Accept: "application/vnd.github+json",
        "User-Agent": "AnisTrade-Worker",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        ref: "main",
        inputs: { command, chat_id: chatId },
      }),
    },
  );
  if (!res.ok) {
    const err = await res.text();
    throw new Error(`GitHub dispatch ${res.status}: ${err}`);
  }
}

async function handleUpdate(update: TelegramUpdate, env: Env): Promise<void> {
  const message = update.message;
  if (!message?.text) return;

  const chatId = String(message.chat.id);
  const text = message.text.trim();
  const cmd = text.split(/\s+/)[0]?.split("@")[0]?.toLowerCase() ?? "";

  if (cmd === "/start") {
    await addSubscriber(env, chatId);
    await telegramApi(env, "sendMessage", {
      chat_id: chatId,
      text: welcomeMessage(chatId),
      parse_mode: "HTML",
      disable_web_page_preview: true,
      reply_markup: { remove_keyboard: true },
    });
    await sendMessage(env, chatId, MENU_HELP);
    return;
  }

  if (cmd === "/menu") {
    await sendMessage(env, chatId, MENU_HELP);
    return;
  }

  const action = SCAN_COMMANDS[cmd];
  if (action) {
    await addSubscriber(env, chatId);
    await sendMessage(env, chatId, "⏳ <i>Analyse en cours…</i>");
    try {
      await triggerGitHubScan(env, action, chatId);
    } catch (e) {
      console.error("GitHub dispatch:", e);
      await sendMessage(
        env,
        chatId,
        "⚠️ Impossible de lancer l'analyse. Réessayez dans quelques instants.",
      );
    }
  }
}

async function setupWebhook(request: Request, env: Env): Promise<Response> {
  const url = new URL(request.url);
  if (url.searchParams.get("secret") !== env.SETUP_SECRET) {
    return new Response("Forbidden", { status: 403 });
  }

  const webhookUrl = `${url.origin}/webhook`;

  const wh = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_TOKEN}/setWebhook`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      url: webhookUrl,
      allowed_updates: ["message"],
      secret_token: env.WEBHOOK_SECRET,
      drop_pending_updates: true,
    }),
  });
  const whData = await wh.json();

  const cmds = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_TOKEN}/setMyCommands`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ commands: BOT_COMMANDS }),
  });
  const cmdsData = await cmds.json();

  // Migration optionnelle depuis ?import=8086813061,5404451034
  const importParam = url.searchParams.get("import");
  if (importParam) {
    const existing = await getChatIds(env);
    const merged = [...new Set([...existing, ...importParam.split(",").map((s) => s.trim())])];
    await saveChatIds(env, merged);
  }

  return Response.json({
    webhook: whData,
    commands: cmdsData,
    webhook_url: webhookUrl,
    subscribers: await getChatIds(env),
  });
}

interface TelegramUpdate {
  update_id?: number;
  message?: {
    text?: string;
    chat: { id: number | string };
  };
}

export default {
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    const url = new URL(request.url);

    if (url.pathname === "/webhook" && request.method === "POST") {
      const token = request.headers.get("X-Telegram-Bot-Api-Secret-Token");
      if (token !== env.WEBHOOK_SECRET) {
        return new Response("Forbidden", { status: 403 });
      }
      const update = (await request.json()) as TelegramUpdate;
      ctx.waitUntil(handleUpdate(update, env));
      return new Response("OK");
    }

    if (url.pathname === "/api/subscribers" && request.method === "GET") {
      const auth = request.headers.get("Authorization");
      if (auth !== `Bearer ${env.WORKER_API_SECRET}`) {
        return new Response("Forbidden", { status: 403 });
      }
      return Response.json({ chat_ids: await getChatIds(env) });
    }

    if (url.pathname === "/setup" && request.method === "GET") {
      return setupWebhook(request, env);
    }

    if (url.pathname === "/" && request.method === "GET") {
      return new Response("AnisTrade Telegram Worker", { status: 200 });
    }

    return new Response("Not Found", { status: 404 });
  },
};
