# Railway Deployment — Step by Step

This guide assumes you've already created a Bale bot via Bale's `@BotFather`
and have an LLM provider key (OpenRouter, OpenAI, or Anthropic).

## 1. Fork the repository

Fork [`balehermes/hermes-bale-gateway`](https://github.com/balehermes/hermes-bale-gateway)
to your GitHub account. (Replace `balehermes` with whatever organization
hosts the fork you want to deploy.)

## 2. Create a Railway project

1. Go to https://railway.app/dashboard and click **New Project → Deploy from
   GitHub repo**.
2. Select your fork.
3. Railway will detect the `Dockerfile` and start a build. The first build
   takes a few minutes because it has to clone `NousResearch/hermes-agent`
   and install the Python dependencies.

## 3. Configure variables

In the **Variables** tab, set at minimum:

| Variable | Value |
|---|---|
| `BALE_BOT_TOKEN` | The token from Bale `@BotFather` |
| `OPENROUTER_API_KEY` | `sk-or-…` (or use `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` + `OPENAI_BASE_URL`) |
| `BALE_ALLOWED_USERS` | Your numeric Bale user ID (strongly recommended) |

**Optional but useful:**

| Variable | Value |
|---|---|
| `BALE_HOME_CHANNEL` | Default chat ID for cron deliveries (numeric, e.g. `987654321`) |
| `BALE_HOME_CHANNEL_NAME` | Human-readable name for the home channel |
| `LLM_MODEL` | e.g. `openai/gpt-5.4-nano`, `anthropic/claude-3-5-sonnet`, … |
| `RADIUS_AUTO_FUND` | `true` to auto-fund the wallet on first boot |
| `GATEWAY_ALLOW_ALL_USERS` | **`false`** unless you explicitly want a public bot |

⚠️ **Never set any `TELEGRAM_*` variable** — the adapter ignores them.

## 4. Add a persistent volume

The Hermes state (wallet keys, session history, ByteRover memory) must
survive redeploys. In the **Settings** tab → **Volumes**:

- Click **Add Volume**.
- Mount path: `/data`.
- Size: at least 1 GB (more if you enable ByteRover).

## 5. Deploy

Click **Deploy**. Watch the build logs. When you see:

```
[bootstrap] getMe OK: bot_id=… username=@…
[bootstrap] Polling started (timeout=30s, interval=1.0s, allow_all=…)
```

the gateway is live.

## 6. Verify in Bale

1. Open Bale Messenger on your phone.
2. Find your bot by username.
3. Tap **Start**.
4. You should see a welcome message from the agent.
5. Send a question — the agent should reply via `parse_mode=HTML` (text is
   formatted with `<b>` bold, `<i>` italic, `<code>` inline code, `<pre>`
   code blocks).

## 7. Verify the admin panel

1. Set `PASS_ADMIN` in Railway Variables to your chosen password.
2. Send `/menu` to the bot.
3. Enter the password when prompted.
4. The admin panel opens with settings, model picker, prompts, and
   broadcast.

## 8. Verify allowlist

If you set `BALE_ALLOWED_USERS=<your-id>`:

1. From your own Bale account: messages are accepted.
2. From a different account: messages are silently dropped (the bot never
   replies). Check Railway logs for `[Bale] Rejecting update from
   unauthorized user_id=…`.

## Common issues

| Symptom | Fix |
|---|---|
| `BALE_BOT_TOKEN is not set` | Make sure you set the variable in the **Variables** tab, not **Deploy settings**. |
| Agent never replies | Open logs; look for `[Bale] getUpdates failed` — usually a network issue. The fallback transport in `bale_network.py` retries known IPv4 literals. |
| `getMe HTTP 401` | Token is wrong or revoked. Generate a new one from Bale `@BotFather`. |
| `getMe HTTP 403` | Bale blocked the bot. Open a ticket with Bale support. |
| Wallet address missing | Check `RADIUS_AUTO_FUND=true` or manually `radius-cli wallet create` once. |
| `agent.log` shows permission errors | The `~/.claude/settings.json` allow-list may be stale. Redeploy — the entrypoint rewrites it on every boot. |

## Tearing down

- **Pause** the Railway service to keep state, or
- **Delete** the service and its `/data` volume to start fresh.