"""Helpers for Bale Bot API identifiers.

Bale ``chat_id`` values follow the same shape as Telegram: a numeric ID for
private chats / groups, or an ``@username``-style string for public channels.
This module centralises the parsing so the adapter never crashes on the
``int(chat_id)`` shape mismatch the same way the Telegram plugin does.
"""

from __future__ import annotations

import re
from typing import Any, Union

# Bale usernames follow the same 5–32 char rule as Telegram; tolerate 4-char
# legacy handles for backwards compatibility.
_BALE_USERNAME_RE = re.compile(r"@[A-Za-z0-9_]{4,32}")


def normalize_bale_chat_id(chat_id: Any) -> Union[int, str]:
    """Return the value as a numeric ``int`` when possible, else a stripped
    string. Never raises.
    """
    chat_id_str = str(chat_id).strip()
    try:
        return int(chat_id_str)
    except (TypeError, ValueError):
        return chat_id_str


def looks_like_bale_username(chat_id: Any) -> bool:
    """True when ``chat_id`` looks like a public Bale ``@username`` target."""
    return bool(_BALE_USERNAME_RE.fullmatch(str(chat_id).strip()))


def parse_bale_username_target(target_ref: Any) -> Union[str, None]:
    """Return the value when it is a ``@username`` target, else ``None``."""
    value = str(target_ref).strip()
    return value if looks_like_bale_username(value) else None


def bale_chat_id_key(chat_id: Any) -> str:
    """Stable string key for a ``chat_id`` (for dict keys / persisted state)."""
    return str(normalize_bale_chat_id(chat_id))