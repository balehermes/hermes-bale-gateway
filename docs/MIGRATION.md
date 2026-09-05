# Migration Notes — Telegram → Bale

This document explains the migration path for operators already running
`radius-workshop/radius-hermes-railway-template` against a Telegram bot and
who want to move the same Hermes instance onto a Bale Messenger bot.

## What changes

### Environment variables

| Telegram (old) | Bale (new) |
|---|---|
| `TELEGRAM_BOT_TOKEN` | `BALE_BOT_TOKEN` |
| `TELEGRAM_ALLOWED_USERS` | `BALE_ALLOWED_USERS` |
| `TELEGRAM_ALLOW_ALL_USERS` | `BALE_ALLOW_ALL_USERS` |
| `TELEGRAM_HOME_CHANNEL` | `BALE_HOME_CHANNEL` |
| `TELEGRAM_HOME_CHANNEL_NAME` | `BALE_HOME_CHANNEL_NAME` |

All other env vars (`OPENROUTER_API_KEY`, `RADIUS_*`, `LINEAR_*`, …) are
**unchanged**.

### Bot API endpoints

| Telegram | Bale |
|---|---|
| `https://api.telegram.org/bot<TOKEN>/…` | `https://tapi.bale.ai/bot<TOKEN>/…` |

Same path structure, same query parameters, same JSON envelope, same file
download endpoint shape. The Hermes gateway runner receives identical
update objects from either platform.

### Bot token

Bale tokens follow the same `<numeric>:<base64-alnum>` format as Telegram
tokens. Length, prefix rules, and revocation semantics are identical.
Acquire one via Bale's `@BotFather` (https://bale.ai/BotFather).

### Parse mode

Telegram supports `MarkdownV2`, `Markdown`, and `HTML`. Bale supports only
`HTML`. The adapter emits HTML exclusively — the agent's reply formatter
emits the same HTML it always has, so output rendering is unchanged.

### Inline keyboards, callback queries

Identical schema.

### Webhooks

| Telegram | Bale |
|---|---|
| `setWebhook` accepts `secret_token` | `setWebhook` accepts `secret_token`; Bale also accepts `X-Bale-Secret` |
| `X-Telegram-Bot-Api-Secret-Token` header | `X-Bale-Secret` header |

The adapter supports both header names; if `BALE_WEBHOOK_SECRET` is set, it
rejects updates missing the matching header. When the secret is unset
(default), the adapter uses long polling — recommended for Railway.

## What does NOT change

- The Hermes core (`NousResearch/hermes-agent`) is **not modified**.
- The Radius wallet, ERC-8004 registry, A2A bridge, skills, plugins — all
  unchanged.
- The persistent `/data` volume layout — unchanged. Your wallet keys,
  session history, and ByteRover memory survive the migration.
- The CLI (`hermes …`) — unchanged.
- The `agent_server/` (FastAPI on `$PORT`) — unchanged.

## Migration steps

1. **Stop the Telegram service** on Railway (but do not delete the volume).
2. **Deploy this repository** as a new Railway service from the same GitHub
   fork (or a fresh fork).
3. **Set the new `BALE_*` variables**. Copy all other variables verbatim
   from the Telegram deployment.
4. **Mount the same `/data` volume** so wallet keys and sessions survive
   (use **Add Volume → Mount Existing Volume** in Railway).
5. **Trigger a redeploy**.
6. **Verify** the `[Bale] getMe OK` log line.
7. **Send `/start`** to the new Bale bot from your Bale Messenger app.

You can run the Telegram and Bale services side-by-side for a transition
period. They share nothing — different bots, different volume paths if you
prefer.

## Rolling back

If something goes wrong, switch back to your previous Telegram deployment
without touching `/data`. Telegram reads the same volume and will pick up
where you left off (session keys, wallet state, etc.).

## Things to double-check

- **Webhook secret.** If you set `BALE_WEBHOOK_SECRET`, generate a strong
  random value. The webhook endpoint is internet-exposed; without the
  secret check, anyone who can reach your Railway URL can pretend to be a
  Bale user.
- **Allowlist.** The new deployment defaults to deny-all. Set
  `BALE_ALLOWED_USERS` to your numeric Bale user ID before going live, or
  you'll see your own messages rejected.
- **Home channel.** Cron jobs and notifications go to `BALE_HOME_CHANNEL`.
  In Telegram, that was a numeric chat ID. On Bale it's the same shape
  (numeric) — just copy the value across.

## When NOT to migrate

- If you depend on Telegram-specific features (Telegram Stars payments,
  Telegram Mini Apps, `forum_topic` mode, etc.) the Bale plugin does not
  emulate them. Stay on the upstream `radius-hermes-railway-template`.
- If your audience is global, Telegram reach is larger. Bale is a regional
  platform — confirm your users actually have Bale accounts before
  migrating.