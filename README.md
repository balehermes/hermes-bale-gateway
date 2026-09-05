# Hermes Agent — Bale Messenger Gateway

[![Platform](https://img.shields.io/badge/platform-Bale%20Messenger-blue.svg)](https://bale.ai)
[![Hermes](https://img.shields.io/badge/powered%20by-Hermes%20Agent-purple.svg)](https://github.com/NousResearch/hermes-agent)
[![Railway](https://img.shields.io/badge/deploy-Railway-blueviolet.svg)](https://railway.app)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](#license)

A Railway-ready deployment of [Hermes Agent](https://github.com/NousResearch/hermes-agent) with a
**Bale Messenger** gateway adapter. This repository is a fork of
[`radius-workshop/radius-hermes-railway-template`](https://github.com/radius-workshop/radius-hermes-railway-template)
with the Telegram platform adapter replaced by a Bale-compatible one.

> **Why Bale?** Bale Messenger is an Iranian instant-messaging platform with a Bot API that closely
> mirrors Telegram's. This project lets an operator run Hermes against a Bale bot instead of a
> Telegram bot while preserving the rest of the Hermes stack (skills, plugins, ERC-8004, A2A,
> Radius wallet, etc.).

---

## Architecture

```
                          ┌────────────────────────────────────┐
                          │     Bale Messenger (User App)     │
                          └────────────────┬───────────────┘
                                           │ HTTPS (webhook or polling)
                                           ▼
┌─────────────────────────────────────────────────────────────┐
│                     Bale Bot (tapi.bale.ai)                │
│                       Bot API: /bot<TOKEN>/...             │
└─────────────────────────────┬───────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│   This Repo: hermes-bale-gateway (Railway Template)       │
│   ┌───────────────────────────────────────────────────┐   │
│   │   plugins/platforms/bale/                         │   │
│   │     adapter.py       (BalePlatformAdapter)        │   │
│   │     bale_network.py  (HTTP fallback transport)    │   │
│   │     bale_ids.py      (chat_id helpers)             │   │
│   └───────────────────────────────────────────────────┘   │
│                             ▲                               │
│                             │  register(ctx)                │
│   ┌───────────────────────────────────────────────────┐   │
│   │   NousResearch/hermes-agent (pip installed)    │   │
│   │     - gateway/run.py (facade)                   │   │
│   │     - gateway/platforms/base.py (ABC)           │   │
│   │     - run_agent.py (core agent loop)             │   │
│   │     - skills/, plugins/, tools/                  │   │
│   └───────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│   LLM Providers: OpenRouter | OpenAI | Anthropic            │
│   Storage: /data/.hermes (Railway persistent volume)       │
│   Radius wallet, ERC-8004 registry, A2A bridge, ...        │
└─────────────────────────────────────────────────────────────┘
```

The Bale plugin lives in `plugins/platforms/bale/` and subclasses
`hermes_agent.gateway.platforms.base.BasePlatformAdapter`. **Zero changes to the Hermes core.**

---

## Bale vs Telegram — API mapping

Bale's Bot API (`https://tapi.bale.ai/bot<TOKEN>/...`) is structurally similar to Telegram's
(`https://api.telegram.org/bot<TOKEN>/...`). Methods used by this adapter:

| Method | Endpoint | Used for |
|---|---|---|
| `getMe` | `GET /getMe` | Validate token on startup |
| `getUpdates` | `GET /getUpdates` | Long-poll incoming messages |
| `setWebhook` | `POST /setWebhook` | Switch to webhook mode |
| `deleteWebhook` | `POST /deleteWebhook` | Switch back to polling |
| `sendMessage` | `POST /sendMessage` | Send text reply |
| `editMessageText` | `POST /editMessageText` | Update sent message |
| `deleteMessage` | `POST /deleteMessage` | Delete sent message |
| `sendPhoto` | `POST /sendPhoto` | Send image |
| `sendDocument` | `POST /sendDocument` | Send document |
| `sendVideo` | `POST /sendVideo` | Send video |
| `getFile` | `GET /getFile` | Resolve file_id → path |
| `answerCallbackQuery` | `POST /answerCallbackQuery` | Inline keyboard ack |

**Differences handled by the adapter:**

- Bale's `message` shape is identical to Telegram's; we keep the field names so the rest of Hermes
  is untouched.
- Bale supports HTML parse mode but has limited Markdown support — this adapter renders output as
  HTML and never uses `parse_mode=Markdown`.
- `caption` is supported on photo/document/video, identical to Telegram.
- Inline keyboards (`inline_keyboard`) work identically.
- Long-poll uses `getUpdates` exactly like Telegram (no webhook secret header required by Bale).

See [`docs/BALE_API.md`](docs/BALE_API.md) for the full mapping and Bale-specific caveats.

---

## Environment variables

All Telegram variables are renamed with a `BALE_` prefix. **Telegram variables are no longer
read**; setting `TELEGRAM_BOT_TOKEN` does nothing.

| Variable | Required | Description |
|---|---|---|
| `BALE_BOT_TOKEN` | ✅ | Bot token from [@BotFather](https://bale.ai/BotFather) on Bale |
| `BALE_ALLOWED_USERS` | optional | Comma-separated numeric user IDs allowed to talk to the bot |
| `BALE_ALLOW_ALL_USERS` | optional | `true` to skip the allowlist (dev only, never in prod) |
| `BALE_HOME_CHANNEL` | optional | Default chat ID for cron / notification delivery |
| `BALE_HOME_CHANNEL_NAME` | optional | Display name for the home channel |
| `BALE_API_BASE_URL` | optional | Override (default `https://tapi.bale.ai`) |
| `BALE_POLLING_TIMEOUT` | optional | Long-poll timeout seconds (default `30`) |
| `BALE_POLLING_INTERVAL` | optional | Sleep between failed polls seconds (default `1.0`) |

Plus the standard Hermes env vars: `OPENROUTER_API_KEY` (or `ANTHROPIC_API_KEY` /
`OPENAI_API_KEY`+`OPENAI_BASE_URL`), `HERMES_HOME=/data/.hermes`, `LLM_MODEL=...`, etc. See
[`.env.example`](.env.example) for the full list.

---

## Quick start (local)

```bash
# 1. Clone
git clone https://github.com/balehermes/hermes-bale-gateway.git
cd hermes-bale-gateway

# 2. Install the Hermes agent core (this provides BasePlatformAdapter, gateway runner, …)
pip install "hermes-agent[messaging,cron,cli,pty] @ git+https://github.com/NousResearch/hermes-agent.git"

# 3. Set the required env vars
export BALE_BOT_TOKEN="123456:ABC-..."
export OPENROUTER_API_KEY="sk-or-..."
# export BALE_ALLOWED_USERS="987654321"

# 4. Run the gateway
hermes gateway run
```

The Bale adapter will log in via `getMe`, register slash commands, and start long-polling
`getUpdates`. Send `/start` to your Bale bot to receive the welcome message.

---

## Railway deployment

1. Fork this repository.
2. Create a new Railway project → "Deploy from GitHub repo".
3. Set the variables from the table above (`BALE_BOT_TOKEN`, `OPENROUTER_API_KEY`, …). **Do not**
   set any `TELEGRAM_*` variable — it will be ignored.
4. Add a volume mounted at `/data` so wallet keys and session state survive redeploys.
5. Trigger a deploy. Watch logs for `[Bale] getMe OK` and `[Bale] Polling started …`.

The included `Dockerfile` does exactly what `radius-hermes-railway-template` does, except it
clones this repo, runs `pip install` for the Bale plugin (which is part of this repo), and runs
`hermes gateway` as the entrypoint.

---

## Security

- **Default-deny on inbound.** If neither `BALE_ALLOWED_USERS` nor `BALE_ALLOW_ALL_USERS` is set,
  the gateway rejects every message with a generic "not authorized" response. **It does not leak
  whether privileged resources exist.**
- **Token redacted from logs.** All Bale transport errors pass through the shared `redact()`
  helper before being logged or returned to the agent.
- **Webhook secret support** is enabled when `BALE_WEBHOOK_SECRET` is set; requests without a
  matching `X-Bale-Secret` header are rejected with 403.

---

## Repository layout

```
hermes-bale-gateway/
├── README.md                       ← you are here
├── .env.example                    ← BALE_* variables (TELEGRAM_* removed)
├── Dockerfile                      ← Railway-compatible build
├── railway.toml                    ← Railway config
├── deploy.sh                       ← helper script
├── plugins/
│   └── platforms/
│       └── bale/                   ← THE Bale plugin
│           ├── __init__.py         ← re-export register()
│           ├── plugin.yaml         ← plugin manifest
│           ├── adapter.py          ← BalePlatformAdapter(BasePlatformAdapter)
│           ├── bale_network.py     ← HTTP fallback transport (DoH + IPv4 literals)
│           ├── bale_ids.py         ← chat_id normalization
│           └── README.md           ← Bale API notes
├── scripts/
│   └── entrypoint.sh               ← bootstrap (validates BALE_BOT_TOKEN)
├── docs/
│   ├── BALE_API.md                 ← Bale ↔ Telegram API mapping
│   ├── DEPLOYMENT.md               ← Railway step-by-step
│   └── MIGRATION.md                ← notes for operators moving off Telegram
└── .dockerignore / .gitignore
```

---

## What this repo is NOT

- This is **not** a fork of `hermes-agent`. The core agent is pip-installed from
  [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent). Changes here only
  affect the Bale platform adapter.
- The Telegram adapter in the upstream `hermes-agent` is **unchanged**. If you want to run both
  Bale and Telegram side-by-side, do that from a separate deployment of the upstream template.
- This repo does **not** modify any Hermes core file. The Bale plugin uses the public
  `BasePlatformAdapter` ABC and `ctx.register_platform()` hook, as documented in
  [`hermes-agent/gateway/platforms/ADDING_A_PLATFORM.md`](https://github.com/NousResearch/hermes-agent/blob/main/gateway/platforms/ADDING_A_PLATFORM.md).

---

## License

MIT (same as the parent `radius-hermes-railway-template`).