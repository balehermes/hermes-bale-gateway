# Bale Platform Plugin

Hermes Agent gateway adapter for [Bale Messenger](https://bale.ai).

This plugin subclasses `BasePlatformAdapter` from
`hermes_agent.gateway.platforms.base` and is registered via
`ctx.register_platform()` in `register()`. **No Hermes core file is
modified.**

See the parent [`README.md`](../../README.md) for deployment instructions
and [`../BALE_API.md`](../BALE_API.md) for the Bale ↔ Telegram API mapping.

## Files

| File | Purpose |
|---|---|
| `__init__.py` | Re-exports `register`. |
| `plugin.yaml` | Plugin manifest (env-var hints, display name). |
| `adapter.py` | `BalePlatformAdapter` — long-poll loop, message dispatch, HTTP wrappers. |
| `bale_network.py` | DoH-based IPv4 fallback transport (mirrors `telegram_network.py`). |
| `bale_ids.py` | `chat_id` normalization helpers. |

## Smoke test

```bash
BALE_BOT_TOKEN="123:abc…" python -m plugins.platforms.bale
```

Runs `getMe` then idles for 5 seconds.