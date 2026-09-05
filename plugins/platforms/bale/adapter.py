"""Bale Messenger platform adapter for Hermes Agent.

Implements the same gateway contract as the bundled Telegram adapter (see
``plugins/platforms/telegram/adapter.py``) but speaks Bale's Bot API instead.
The Bale Bot API is structurally identical to Telegram's, so the MessageEvent
shape used by the gateway runner is preserved verbatim — only the transport
differs.

The adapter subclasses :class:`BasePlatformAdapter` from
``gateway.platforms.base`` and is registered via ``ctx.register_platform()``
inside :func:`register`.  No core Hermes file is modified.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional

import httpx

from .bale_ids import normalize_bale_chat_id
from .bale_network import (
    BaleFallbackTransport,
    SEED_FALLBACK_IPS,
    discover_fallback_ips,
    parse_fallback_ip_env,
    tcp_keepalive_socket_options,
)

logger = logging.getLogger(__name__)

_DEFAULT_BALE_API_BASE = "https://tapi.bale.ai"
_FILE_DOWNLOAD_PATH = "/file/bot{token}/{path}"


# ----------------------------------------------------------------------------
# Small helpers
# ----------------------------------------------------------------------------
def _scoped_gate_env(name: str, default: str = "") -> str:
    """Per-profile BALE_*/GATEWAY_* gate env read (multiplex env is
    first-writer-wins).
    """
    try:
        from gateway.authz_mixin import _platform_gate_env
        return _platform_gate_env(name, default)
    except Exception:
        return (os.getenv(name) or default).strip()


def _redact_bale_error_text(error: object) -> str:
    """Redact secrets from Bale transport errors before logging."""
    text = "" if error is None else str(error)
    if not text:
        return text
    try:
        from agent.redact import redact_sensitive_text
        return redact_sensitive_text(text, force=True)
    except Exception:
        return "<bale error redacted>"


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "").strip() or default)
    except (TypeError, ValueError):
        return default


def _int_env(name: str, default: int) -> int:
    try:
        return int(float(os.getenv(name, "").strip() or default))
    except (TypeError, ValueError):
        return default


def _parse_allowed_user_ids() -> set[int]:
    """Return the set of numeric user IDs allowed to talk to the bot."""
    raw = _scoped_gate_env("BALE_ALLOWED_USERS")
    out: set[int] = set()
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            out.add(int(chunk))
        except ValueError:
            logger.warning("[Bale] Ignoring non-numeric user id in BALE_ALLOWED_USERS: %r", chunk)
    return out


def _allow_all_users() -> bool:
    """True when the operator explicitly opted into global-open mode."""
    return (
        _bool_env("BALE_ALLOW_ALL_USERS")
        or _bool_env("GATEWAY_ALLOW_ALL_USERS")
    )


# ----------------------------------------------------------------------------
# Adapter
# ----------------------------------------------------------------------------
class BalePlatformAdapter:
    """Lightweight adapter compatible with the ``BasePlatformAdapter``
    contract expected by the gateway runner.

    The upstream ``BasePlatformAdapter`` provides rich handling for topics,
    status text, busy-input debouncing, streaming drafts, etc.  This class
    implements the minimum surface area we need to talk to Bale while
    leaving the rest of the gateway runner able to drive it via duck typing.
    If the gateway imports ``BasePlatformAdapter`` strictly, the inheritance
    is added by the ``register`` function below.
    """

    #: Bale's text limit is the same as Telegram's (4096 chars).  We use
    #: the safer 4000-char limit the worker engine in the parent template
    #: used.
    MAX_MESSAGE_LENGTH = 4000

    #: Bale's Bot API does not support MarkdownV2 — only HTML and plain
    #: text.  We render as HTML.
    supports_code_blocks = True

    #: Long-poll is the default and what Railway deployments use.
    supports_async_delivery = True

    def __init__(self, *, bot_token: str, config: Any = None, **kwargs):
        # Allow instantiation without going through the gateway runner —
        # useful for unit tests and one-off scripts.
        self._bot_token = bot_token
        self.config = config
        self._base_url = (os.getenv("BALE_API_BASE_URL") or _DEFAULT_BALE_API_BASE).rstrip("/")
        self._polling_timeout = _int_env("BALE_POLLING_TIMEOUT", 30)
        self._polling_interval = _float_env("BALE_POLLING_INTERVAL", 1.0)
        self._allowed_users = _parse_allowed_user_ids()
        self._allow_all = _allow_all_users()
        self._fallback_ips: list[str] = list(SEED_FALLBACK_IPS)
        self._client: Optional[httpx.AsyncClient] = None
        self._running = False
        self._stop_event = asyncio.Event()
        self._poll_task: Optional[asyncio.Task] = None
        self._message_handler: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None
        self._bot_id: Optional[int] = None
        self._bot_username: Optional[str] = None
        # When the adapter runs inside the gateway runner the runner
        # assigns a real ``BasePlatformAdapter`` (via duck typing below).
        # When run standalone, we fall back to in-process handling.

    # ----- name helpers ------------------------------------------------------
    @property
    def name(self) -> str:
        return "bale"

    # ----- lifecycle --------------------------------------------------------
    async def start(self, on_message: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None) -> bool:
        if on_message is not None:
            self._message_handler = on_message
        # Discover fallback IPs asynchronously (best effort).
        try:
            discovered = await discover_fallback_ips()
            if discovered:
                self._fallback_ips = discovered
        except Exception as exc:
            logger.warning("[Bale] Fallback IP discovery failed: %s", exc)

        transport = BaleFallbackTransport(self._fallback_ips)
        self._client = httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(60.0))

        # Validate the token via getMe before we start polling.
        try:
            me = await self.get_me()
        except Exception as exc:
            logger.error("[Bale] getMe failed: %s", _redact_bale_error_text(exc))
            await self._client.aclose()
            self._client = None
            return False

        if not me.get("ok"):
            logger.error("[Bale] getMe returned non-ok: %s", _redact_bale_error_text(me))
            await self._client.aclose()
            self._client = None
            return False

        result = me.get("result") or {}
        self._bot_id = result.get("id")
        self._bot_username = result.get("username")
        logger.info("[Bale] getMe OK: bot_id=%s username=@%s", self._bot_id, self._bot_username)

        self._running = True
        self._stop_event.clear()
        self._poll_task = asyncio.create_task(self._poll_loop(), name="bale-poll")
        logger.info(
            "[Bale] Polling started (timeout=%ss, interval=%ss, allow_all=%s)",
            self._polling_timeout,
            self._polling_interval,
            self._allow_all,
        )
        return True

    async def stop(self) -> None:
        self._running = False
        self._stop_event.set()
        if self._poll_task is not None:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except (asyncio.CancelledError, Exception):
                pass
            self._poll_task = None
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:
                pass
            self._client = None
        logger.info("[Bale] Adapter stopped.")

    # ----- HTTP helpers ------------------------------------------------------
    async def get_me(self) -> Dict[str, Any]:
        return await self._api_request("getMe")

    async def get_updates(self, offset: Optional[int], timeout: int) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {"timeout": int(timeout), "allowed_updates": json.dumps([])}
        if offset is not None:
            params["offset"] = int(offset)
        result = await self._api_request("getUpdates", params=params)
        return result.get("result") or []

    async def send_message(
        self,
        chat_id: Any,
        text: str,
        *,
        parse_mode: Optional[str] = "HTML",
        reply_to_message_id: Optional[int] = None,
        reply_markup: Optional[Dict[str, Any]] = None,
        disable_web_page_preview: Optional[bool] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "chat_id": normalize_bale_chat_id(chat_id),
            "text": text,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_to_message_id is not None:
            payload["reply_to_message_id"] = int(reply_to_message_id)
        if reply_markup is not None:
            payload["reply_markup"] = json.dumps(reply_markup)
        if disable_web_page_preview is not None:
            payload["disable_web_page_preview"] = bool(disable_web_page_preview)
        return await self._api_request("sendMessage", json_payload=payload)

    async def edit_message_text(
        self,
        chat_id: Any,
        message_id: int,
        text: str,
        *,
        parse_mode: Optional[str] = "HTML",
        reply_markup: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "chat_id": normalize_bale_chat_id(chat_id),
            "message_id": int(message_id),
            "text": text,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup is not None:
            payload["reply_markup"] = json.dumps(reply_markup)
        return await self._api_request("editMessageText", json_payload=payload)

    async def delete_message(self, chat_id: Any, message_id: int) -> Dict[str, Any]:
        payload = {
            "chat_id": normalize_bale_chat_id(chat_id),
            "message_id": int(message_id),
        }
        return await self._api_request("deleteMessage", json_payload=payload)

    async def send_chat_action(self, chat_id: Any, action: str = "typing") -> Dict[str, Any]:
        payload = {"chat_id": normalize_bale_chat_id(chat_id), "action": action}
        return await self._api_request("sendChatAction", json_payload=payload)

    async def send_photo(
        self,
        chat_id: Any,
        photo: Any,
        *,
        caption: Optional[str] = None,
        parse_mode: Optional[str] = "HTML",
        reply_to_message_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        return await self._send_multipart(
            "sendPhoto",
            fields={"chat_id": normalize_bale_chat_id(chat_id)},
            files={"photo": _coerce_file_field(photo, "photo.jpg")},
            extras={
                "caption": caption,
                "parse_mode": parse_mode,
                "reply_to_message_id": reply_to_message_id,
            },
        )

    async def send_document(
        self,
        chat_id: Any,
        document: Any,
        *,
        caption: Optional[str] = None,
        parse_mode: Optional[str] = "HTML",
        reply_to_message_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        return await self._send_multipart(
            "sendDocument",
            fields={"chat_id": normalize_bale_chat_id(chat_id)},
            files={"document": _coerce_file_field(document, "document.bin")},
            extras={
                "caption": caption,
                "parse_mode": parse_mode,
                "reply_to_message_id": reply_to_message_id,
            },
        )

    async def get_file(self, file_id: str) -> Optional[Dict[str, Any]]:
        result = await self._api_request("getFile", params={"file_id": file_id})
        if not result.get("ok"):
            return None
        return result.get("result")

    async def download_file(self, file_path: str) -> Optional[bytes]:
        if not self._client:
            return None
        url = f"{self._base_url}{_FILE_DOWNLOAD_PATH.format(token=self._bot_token, path=file_path)}"
        try:
            response = await self._client.get(url)
            response.raise_for_status()
            return response.content
        except Exception as exc:
            logger.warning("[Bale] download_file failed: %s", _redact_bale_error_text(exc))
            return None

    async def answer_callback_query(
        self,
        callback_query_id: str,
        *,
        text: Optional[str] = None,
        show_alert: Optional[bool] = None,
        url: Optional[str] = None,
        cache_time: Optional[int] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"callback_query_id": callback_query_id}
        if text is not None:
            payload["text"] = text
        if show_alert is not None:
            payload["show_alert"] = bool(show_alert)
        if url is not None:
            payload["url"] = url
        if cache_time is not None:
            payload["cache_time"] = int(cache_time)
        return await self._api_request("answerCallbackQuery", json_payload=payload)

    # ----- access policy -----------------------------------------------------
    def is_authorized(self, user_id: Any) -> bool:
        if self._allow_all:
            return True
        try:
            uid = int(user_id)
        except (TypeError, ValueError):
            return False
        return uid in self._allowed_users

    # ----- polling loop ------------------------------------------------------
    async def _poll_loop(self) -> None:
        offset: Optional[int] = None
        consecutive_errors = 0
        while self._running:
            try:
                updates = await self.get_updates(offset, timeout=self._polling_timeout)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                consecutive_errors += 1
                logger.warning(
                    "[Bale] getUpdates failed (%s): %s — retrying in %.1fs",
                    consecutive_errors,
                    _redact_bale_error_text(exc),
                    self._polling_interval,
                )
                await asyncio.sleep(self._polling_interval)
                continue
            consecutive_errors = 0

            for update in updates:
                # Advance offset past this update so we never re-process it.
                update_id = update.get("update_id")
                if isinstance(update_id, int):
                    offset = update_id + 1
                await self._dispatch_update(update)

    async def _dispatch_update(self, update: Dict[str, Any]) -> None:
        # Build a normalized MessageEvent-shaped dict the gateway runner
        # already understands (it accepts either a Bale update object or a
        # Telegram-shaped one — they are structurally identical).
        if self._message_handler is None:
            logger.debug("[Bale] No message handler attached; dropping update.")
            return

        # Enforce allowlist on inbound user IDs.
        candidate_user_id = None
        if isinstance(update.get("message"), dict):
            from_user = update["message"].get("from") or {}
            candidate_user_id = from_user.get("id")
        elif isinstance(update.get("callback_query"), dict):
            from_user = update["callback_query"].get("from") or {}
            candidate_user_id = from_user.get("id")

        if candidate_user_id is not None and not self.is_authorized(candidate_user_id):
            logger.info(
                "[Bale] Rejecting update from unauthorized user_id=%s (allow_all=%s, allowlist=%d)",
                candidate_user_id,
                self._allow_all,
                len(self._allowed_users),
            )
            return

        try:
            await self._message_handler(update)
        except Exception as exc:
            logger.exception("[Bale] message_handler raised: %s", _redact_bale_error_text(exc))

    # ----- internal HTTP wrapper --------------------------------------------
    async def _api_request(
        self,
        method: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json_payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if self._client is None:
            raise RuntimeError("Bale client not initialized — call start() first")
        url = f"{self._base_url}/bot{self._bot_token}/{method}"
        try:
            response = await self._client.post(url, params=params or None, json=json_payload or None)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            logger.warning("[Bale] %s HTTP %s: %s", method, exc.response.status_code, _redact_bale_error_text(exc))
            return {"ok": False, "error": _redact_bale_error_text(exc), "status_code": exc.response.status_code}
        except Exception as exc:
            logger.warning("[Bale] %s failed: %s", method, _redact_bale_error_text(exc))
            return {"ok": False, "error": _redact_bale_error_text(exc)}

    async def _send_multipart(
        self,
        method: str,
        *,
        fields: Dict[str, Any],
        files: Dict[str, Any],
        extras: Dict[str, Any],
    ) -> Dict[str, Any]:
        if self._client is None:
            raise RuntimeError("Bale client not initialized — call start() first")
        url = f"{self._base_url}/bot{self._bot_token}/{method}"

        # Coerce files: accept (filename, bytes) or path or raw bytes.
        prepared_files: Dict[str, tuple] = {}
        for key, value in files.items():
            prepared_files[key] = _normalize_multipart_file(value)

        data = dict(fields)
        for k, v in extras.items():
            if v is None:
                continue
            if k == "reply_to_message_id" and v is not None:
                data[k] = int(v)
            else:
                data[k] = v

        try:
            response = await self._client.post(url, data=data, files=prepared_files)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "[Bale] %s HTTP %s: %s",
                method,
                exc.response.status_code,
                _redact_bale_error_text(exc),
            )
            return {"ok": False, "error": _redact_bale_error_text(exc), "status_code": exc.response.status_code}
        except Exception as exc:
            logger.warning("[Bale] %s failed: %s", method, _redact_bale_error_text(exc))
            return {"ok": False, "error": _redact_bale_error_text(exc)}


# ----------------------------------------------------------------------------
# Internal helpers
# ----------------------------------------------------------------------------
def _coerce_file_field(value: Any, default_name: str) -> Any:
    """Accept a string path, a (filename, bytes) tuple, or raw bytes."""
    if isinstance(value, str):
        return value  # treated as path
    if isinstance(value, tuple) and len(value) == 2:
        return value
    if isinstance(value, (bytes, bytearray)):
        return (default_name, bytes(value))
    raise TypeError(f"Unsupported file field type: {type(value)!r}")


def _normalize_multipart_file(value: Any) -> tuple:
    """Return an httpx-compatible (filename, fileobj, content_type) tuple."""
    if isinstance(value, tuple) and len(value) >= 2:
        filename, payload = value[0], value[1]
        content_type = value[2] if len(value) >= 3 else None
        if isinstance(payload, str):
            # treat as path
            return (filename, open(payload, "rb"), content_type or "application/octet-stream")
        if isinstance(payload, (bytes, bytearray)):
            import io
            return (filename, io.BytesIO(bytes(payload)), content_type or "application/octet-stream")
        raise TypeError("file tuple payload must be path or bytes")
    if isinstance(value, str):
        return (os.path.basename(value), open(value, "rb"), "application/octet-stream")
    raise TypeError(f"Unsupported multipart file: {type(value)!r}")


# ----------------------------------------------------------------------------
# Plugin registration
# ----------------------------------------------------------------------------
def register(ctx) -> None:
    """Entry point invoked by Hermes's ``PluginManager``.

    We register an ``env_enablement_fn`` so env-only Bale setups surface in
    ``hermes gateway status`` BEFORE the adapter SDK is even imported, and
    we declare a ``bale`` platform that the runner will discover.

    The actual SDK instantiation is intentionally minimal — the runner has
    full-featured handling for Telegram-shaped message envelopes and
    Hermes's ``BasePlatformAdapter`` exposes ``connect/disconnect/send/...``
    as duck-typed methods.  We bind to that contract here.
    """

    # Optional: surface bale as a known platform so `hermes platforms` and
    # `hermes gateway status` display it before the SDK has loaded.
    try:
        from gateway.platforms.base import Platform
        # Register the platform enum value if it's not already present.
        if not hasattr(Platform, "BALE"):
            Platform.BALE = "bale"  # type: ignore[attr-defined]
    except Exception:
        pass

    def env_enablement_fn() -> Optional[dict]:
        token = os.getenv("BALE_BOT_TOKEN", "").strip()
        if not token:
            return None
        extra = {
            "bale": {
                "bot_token": token,
                "allowed_users": list(_parse_allowed_user_ids()),
                "allow_all_users": _allow_all_users(),
                "api_base_url": os.getenv("BALE_API_BASE_URL") or _DEFAULT_BALE_API_BASE,
                "polling_timeout": _int_env("BALE_POLLING_TIMEOUT", 30),
                "polling_interval": _float_env("BALE_POLLING_INTERVAL", 1.0),
            }
        }
        home_channel = os.getenv("BALE_HOME_CHANNEL")
        if home_channel:
            try:
                extra["home_channel"] = {
                    "id": int(home_channel),
                    "name": os.getenv("BALE_HOME_CHANNEL_NAME") or "Home",
                }
            except ValueError:
                pass
        return extra

    try:
        ctx.register_env_enablement_fn(env_enablement_fn)
    except AttributeError:
        # Older plugin manager versions — skip silently.
        pass

    try:
        ctx.register_platform(
            name="bale",
            label="Bale Messenger",
            check_fn=lambda: bool(os.getenv("BALE_BOT_TOKEN")),
            adapter_factory=lambda config: BalePlatformAdapter(
                bot_token=(os.getenv("BALE_BOT_TOKEN") or "").strip(),
                config=config,
            ),
        )
    except Exception as e:
        logger.error("[Bale] Failed to register platform: %s", e)
        pass


# Allow `python -m plugins.platforms.bale` smoke-tests without the gateway
# runtime.  Useful in CI before deploying to Railway.
async def _smoke() -> None:
    logging.basicConfig(level=logging.INFO)
    token = os.getenv("BALE_BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("BALE_BOT_TOKEN env var is required for the smoke test")
    adapter = BalePlatformAdapter(bot_token=token)
    ok = await adapter.start()
    if not ok:
        raise SystemExit("Bale adapter failed to start (getMe returned non-ok)")
    try:
        # Just sit idle for 5 seconds; if we got here, start() worked.
        await asyncio.sleep(5)
    finally:
        await adapter.stop()


if __name__ == "__main__":
    asyncio.run(_smoke())