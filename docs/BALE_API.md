# Bale Bot API — Operator Notes

Bale Messenger publishes a Bot API structurally identical to Telegram's. This
document records the method mapping used by `plugins/platforms/bale/adapter.py`
and the platform-specific quirks the adapter handles.

## Base URL

```
https://tapi.bale.ai
```

All endpoints use HTTPS. Override with `BALE_API_BASE_URL` for testing or
mirror deployments. The file-download endpoint is `/file/bot<TOKEN>/<path>`
(same shape as Telegram).

## Methods used by the adapter

| Method | Endpoint | Direction | Notes |
|---|---|---|---|
| `getMe` | `GET /bot<TOKEN>/getMe` | startup | Validates the token. Bale returns `{"ok":true,"result":{...}}`. |
| `getUpdates` | `GET /bot<TOKEN>/getUpdates` | inbound | Long-poll with `timeout=<secs>` and `offset=<last_id+1>`. |
| `sendMessage` | `POST /bot<TOKEN>/sendMessage` | outbound | Supports `parse_mode=HTML`. MarkdownV2 is not supported by Bale. |
| `editMessageText` | `POST /bot<TOKEN>/editMessageText` | outbound | Used to live-update the "processing…" placeholder. |
| `deleteMessage` | `POST /bot<TOKEN>/deleteMessage` | outbound | Used to retract the admin panel on `/close`. |
| `sendPhoto` | `POST /bot<TOKEN>/sendPhoto` | outbound | Multipart upload. `caption` optional, HTML parse_mode. |
| `sendDocument` | `POST /bot<TOKEN>/sendDocument` | outbound | Multipart upload. Used for prompt file exports. |
| `sendChatAction` | `POST /bot<TOKEN>/sendChatAction` | outbound | `typing` heartbeat. Bale keeps the bubble for ~5s. |
| `getFile` | `GET /bot<TOKEN>/getFile?file_id=...` | inbound | Returns `{file_path, file_size}` for downloading media. |
| `answerCallbackQuery` | `POST /bot<TOKEN>/answerCallbackQuery` | outbound | Acknowledge inline-keyboard taps. |
| `setWebhook` | `POST /bot<TOKEN>/setWebhook` | optional | Used only if `BALE_WEBHOOK_URL` is set. |
| `deleteWebhook` | `POST /bot<TOKEN>/deleteWebhook` | optional | Used when switching back to polling. |

Methods **not used** by this adapter (and the closest safe Bale behavior):

- `sendVideo` / `sendAnimation` / `sendVoice` / `sendAudio` — implemented as
  `sendDocument` fallback in the agent core, which already maps media to the
  generic document endpoint. No adapter-side code needed.
- `pinChatMessage` / `unpinChatMessage` — Hermes core does not pin messages.

## Update shape

Bale updates share Telegram's JSON shape exactly:

```json
{
  "update_id": 12345,
  "message": {
    "message_id": 1,
    "from": {"id": 987654321, "first_name": "…", "username": "…"},
    "chat": {"id": 987654321, "type": "private"},
    "date": 1700000000,
    "text": "hello"
  }
}
```

This is **important**: by keeping the same field names, the Hermes gateway
runner, the agent's tool schema, and all upstream message normalization code
work without modification.

## Differences from Telegram

1. **HTML only.** Bale's Bot API does not implement Telegram's `parse_mode=MarkdownV2`.
   The adapter always sends `parse_mode=HTML` and the agent's reply formatter
   emits safe HTML (escaping `<`, `>`, `&`).
2. **Webhook secret header.** Bale accepts `X-Bale-Secret` if the bot was
   configured with one. The adapter reads `BALE_WEBHOOK_SECRET` and rejects
   updates that don't carry a matching header. When the secret is unset
   (default), the adapter uses long polling.
3. **No silent-forward capability.** Bale does not support Telegram's
   `protect_content` or `message_effect_id`. Skip them.
4. **Sticker / location / contact** payloads are accepted but currently
   ignored by the Hermes gateway runner — same behavior as Telegram.
5. **Inline keyboards.** Identical schema.

## Token acquisition

A Bale bot token is created via Bale's `@BotFather` analog (typically
referenced as `bale.ai/BotFather`). The token format is
`<numeric>:<base64-alnum>`, same as Telegram.

## Network hardening

`bale_network.py` provides a DoH-aware IPv4-literal fallback transport so
deployment networks with broken DNS or blackholed IPv6 paths can't pin the
gateway's long-poll loop. Disable by setting `BALE_FALLBACK_IPS=""` if
your network can reach `tapi.bale.ai` directly.

## Local smoke test

```bash
BALE_BOT_TOKEN="123:abc…" python -m plugins.platforms.bale
```

Runs `getMe` then idles for 5 seconds; the exit code is 0 if `getMe`
returned `ok:true`. Useful in CI.