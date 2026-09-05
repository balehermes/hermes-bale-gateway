"""Bale network helpers.

Mirrors the structure of ``plugins/platforms/telegram/telegram_network.py``
but targets ``tapi.bale.ai`` instead of ``api.telegram.org``.  The Bale Bot
API is structurally identical to Telegram's; the only practical differences
relevant here are:

* the hostname (so DoH / seed-IP fallback targets differ),
* the default port (HTTPS, same as Telegram),
* Bale does not require ``setWebhook`` to use ``getUpdates`` polling.

The transport below is a hostname-preserving fallback that retries known
IPv4 literals first, dual-stack hostname last.  Without this, a blackholed
IPv6 path on the deployment network can pin ``getUpdates`` forever (Windows
does not enable ``SO_KEEPALIVE`` on new sockets by default — see the
Telegram plugin's notes for the same root cause and the upstream issue
references).
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
from typing import Iterable, Optional

import httpx

logger = logging.getLogger(__name__)

_BALE_API_HOST = "tapi.bale.ai"

_TCP_KEEPALIVE_IDLE_S = 30
_TCP_KEEPALIVE_INTERVAL_S = 10
_TCP_KEEPALIVE_COUNT = 3


def tcp_keepalive_socket_options() -> list[tuple[int, int, int]]:
    """``setsockopt`` tuples for httpx ``socket_options`` — always
    ``SO_KEEPALIVE``, plus idle/interval/count where exposed.
    """
    options: list[tuple[int, int, int]] = [(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)]
    idle = getattr(socket, "TCP_KEEPIDLE", None) or getattr(socket, "TCP_KEEPALIVE", None)
    for opt, value in (
        (idle, _TCP_KEEPALIVE_IDLE_S),
        (getattr(socket, "TCP_KEEPINTVL", None), _TCP_KEEPALIVE_INTERVAL_S),
        (getattr(socket, "TCP_KEEPCNT", None), _TCP_KEEPALIVE_COUNT),
    ):
        if opt is not None:
            options.append((socket.IPPROTO_TCP, opt, value))
    return options


# DNS-over-HTTPS providers — discover Bale API IPs that the (possibly
# unreachable) local resolver may not return.  Bounded so connect() isn't
# delayed.
_DOH_TIMEOUT = 4.0
_DOH_PROVIDERS: list[dict] = [
    {"url": "https://dns.google/resolve", "params": {"name": _BALE_API_HOST, "type": "A"}, "headers": {}},
    {
        "url": "https://cloudflare-dns.com/dns-query",
        "params": {"name": _BALE_API_HOST, "type": "A"},
        "headers": {"Accept": "application/dns-json"},
    },
]
# Last-resort IPv4 Bot API endpoints for Bale.  Bale publishes a documented
# range (the platform is operated from a single /24); update this list when
# Bale announces new endpoints.
SEED_FALLBACK_IPS: list[str] = ["185.143.47.4"]
_UNSET = object()


def _resolve_proxy_url(target_hosts=None) -> Optional[str]:
    try:
        from gateway.platforms.base import resolve_proxy_url  # env vars + macOS system proxy
        return resolve_proxy_url("BALE_PROXY", target_hosts=target_hosts)
    except Exception:
        # Outside the gateway runtime (e.g. unit tests) — never fail the
        # import just because the helper isn't available.
        return None


class BaleFallbackTransport(httpx.AsyncBaseTransport):
    """Reach the Bale Bot API via known IPv4 literals first, dual-stack
    hostname last.  Host + SNI stay on ``tapi.bale.ai`` (like
    ``curl --resolve``) so a blackholed IPv6 AAAA can't pin
    ``initialize()``.
    """

    # Bound every pool — httpx's 100-connection default × (wedged endpoint +
    # seed IPs) can outgrow the fd limit.
    _POOL_LIMITS = httpx.Limits(max_connections=8, max_keepalive_connections=4)

    def __init__(self, fallback_ips: Iterable[str], **transport_kwargs):
        self._fallback_ips = list(dict.fromkeys(_normalize_fallback_ips(fallback_ips)))
        proxy_url = _resolve_proxy_url(target_hosts=[_BALE_API_HOST, *self._fallback_ips])
        if proxy_url and "proxy" not in transport_kwargs:
            transport_kwargs["proxy"] = proxy_url
        transport_kwargs.setdefault("limits", self._POOL_LIMITS)
        transport_kwargs.setdefault("socket_options", tcp_keepalive_socket_options())
        self._transport_kwargs = transport_kwargs
        self._primary = httpx.AsyncHTTPTransport(**transport_kwargs)
        self._primary_lock = asyncio.Lock()
        self._primary_closed = False
        self._fallbacks: dict[str, httpx.AsyncHTTPTransport] = {}
        self._fallback_lock = asyncio.Lock()
        self._sticky_ip: object = _UNSET
        self._sticky_lock = asyncio.Lock()

    async def _get_fallback(self, ip: str) -> httpx.AsyncHTTPTransport:
        async with self._fallback_lock:
            transport = self._fallbacks.get(ip)
            if transport is None:
                transport = httpx.AsyncHTTPTransport(**self._transport_kwargs)
                self._fallbacks[ip] = transport
            return transport

    async def _reset_primary(self, transport: httpx.AsyncHTTPTransport) -> None:
        async with self._primary_lock:
            if self._primary_closed or transport is not self._primary:
                return
            self._primary = httpx.AsyncHTTPTransport(**self._transport_kwargs)
        try:
            await transport.aclose()
        except Exception as exc:
            logger.debug("[Bale] Error closing primary transport: %s", exc)

    async def _reset_fallback(self, ip: str) -> None:
        async with self._fallback_lock:
            transport = self._fallbacks.pop(ip, None)
        if transport is None:
            return
        try:
            await transport.aclose()
        except Exception as exc:
            logger.debug("[Bale] Error closing fallback transport %s: %s", ip, exc)

    def _attempt_order(self) -> list[Optional[str]]:
        order: list[Optional[str]] = []
        if self._sticky_ip is not _UNSET:
            order.append(None if self._sticky_ip is None else str(self._sticky_ip))
        order.extend(ip for ip in self._fallback_ips if ip not in order)
        if None not in order:
            order.append(None)
        return order

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.host != _BALE_API_HOST or not self._fallback_ips:
            return await self._primary.handle_async_request(request)
        last_error: Exception | None = None
        for ip in self._attempt_order():
            candidate = request if ip is None else _rewrite_request_for_ip(request, ip)
            transport = self._primary if ip is None else await self._get_fallback(ip)
            try:
                response = await transport.handle_async_request(candidate)
                if self._sticky_ip is _UNSET or self._sticky_ip != ip:
                    async with self._sticky_lock:
                        if self._sticky_ip is _UNSET or self._sticky_ip != ip:
                            self._sticky_ip = ip
                            if ip is not None:
                                log = logger.warning if last_error is not None else logger.info
                                log("[Bale] Using sticky IPv4 Bale API path %s", ip)
                return response
            except Exception as exc:
                last_error = exc
                if not _is_retryable_connect_error(exc):
                    raise
                if self._sticky_ip is not _UNSET and ip == self._sticky_ip:
                    async with self._sticky_lock:
                        if self._sticky_ip is not _UNSET and self._sticky_ip == ip:
                            self._sticky_ip = _UNSET
                            logger.warning("[Bale] Sticky Bale path %s failed; re-walking IPv4 literals", ip)
                if ip is None:
                    await self._reset_primary(transport)
                    logger.warning("[Bale] Dual-stack tapi.bale.ai path failed (%s)", exc)
                    continue
                logger.warning("[Bale] IPv4 Bale API IP %s failed: %s", ip, exc)
                await self._reset_fallback(ip)
                continue
        if last_error is None:
            raise RuntimeError("All Bale fallback IPs exhausted but no error was recorded")
        raise last_error

    async def aclose(self) -> None:
        async with self._primary_lock:
            self._primary_closed = True
            primary = self._primary
        await primary.aclose()
        async with self._fallback_lock:
            transports = list(self._fallbacks.values())
            self._fallbacks.clear()
        for transport in transports:
            await transport.aclose()


def _normalize_fallback_ips(values: Iterable[str]) -> list[str]:
    normalized: list[str] = []
    for value in values:
        raw = str(value).strip()
        if not raw:
            continue
        try:
            addr = ipaddress.ip_address(raw)
        except ValueError:
            logger.warning("Ignoring invalid Bale fallback IP: %r", raw)
            continue
        if addr.version != 4:
            logger.warning("Ignoring non-IPv4 Bale fallback IP: %s", raw)
        elif addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_unspecified:
            logger.warning("Ignoring private/internal Bale fallback IP: %s", raw)
        else:
            normalized.append(str(addr))
    return normalized


def parse_fallback_ip_env(value: Optional[str]) -> list[str]:
    return _normalize_fallback_ips(part.strip() for part in value.split(",")) if value else []


def _resolve_system_dns() -> set[str]:
    try:
        results = socket.getaddrinfo(_BALE_API_HOST, 443, socket.AF_INET)
        return {addr[4][0] for addr in results}
    except Exception:
        return set()


async def _query_doh_provider(client: httpx.AsyncClient, provider: dict) -> list[str]:
    try:
        resp = await client.get(provider["url"], params=provider["params"], headers=provider["headers"])
        resp.raise_for_status()
        data = resp.json()
        ips: list[str] = []
        for answer in data.get("Answer", []):
            if answer.get("type") != 1:  # A record
                continue
            raw = answer.get("data", "").strip()
            try:
                ipaddress.ip_address(raw)
            except ValueError:
                continue
            ips.append(raw)
        return ips
    except Exception as exc:
        logger.debug("DoH query to %s failed: %s", provider["url"], exc)
        return []


async def discover_fallback_ips() -> list[str]:
    """Resolve ``tapi.bale.ai`` via Google + Cloudflare DoH; unique A records,
    in order.  Falls back to :data:`SEED_FALLBACK_IPS` only when DoH yields
    nothing usable.
    """
    async with httpx.AsyncClient(timeout=httpx.Timeout(_DOH_TIMEOUT)) as client:
        system_dns_task = asyncio.ensure_future(asyncio.to_thread(_resolve_system_dns))
        results = await asyncio.gather(
            *[_query_doh_provider(client, p) for p in _DOH_PROVIDERS],
            return_exceptions=True,
        )
    system_ips: set[str] = set()
    try:
        system_result = await asyncio.wait_for(system_dns_task, timeout=_DOH_TIMEOUT)
        if isinstance(system_result, set):
            system_ips = system_result
    except Exception:
        logger.debug("System-DNS resolution for %s did not complete in time", _BALE_API_HOST)
    doh_ips = [ip for r in results if isinstance(r, list) for ip in r]
    validated = _normalize_fallback_ips(list(dict.fromkeys(doh_ips)))
    if validated:
        logger.debug("Discovered Bale fallback IPs via DoH: %s", ", ".join(validated))
        return validated
    logger.info(
        "DoH discovery yielded no usable IPs (system DNS: %s); using seed fallback IPs %s",
        ", ".join(system_ips) or "unknown",
        ", ".join(SEED_FALLBACK_IPS),
    )
    return list(SEED_FALLBACK_IPS)


def _rewrite_request_for_ip(request: httpx.Request, ip: str) -> httpx.Request:
    original_host = request.url.host or _BALE_API_HOST
    url = request.url.copy_with(host=ip)
    headers = request.headers.copy()
    headers["host"] = original_host
    extensions = dict(request.extensions)
    extensions["sni_hostname"] = original_host
    return httpx.Request(
        method=request.method,
        url=url,
        headers=headers,
        stream=request.stream,
        extensions=extensions,
    )


def _is_retryable_connect_error(exc: Exception) -> bool:
    return isinstance(exc, (httpx.ConnectTimeout, httpx.ConnectError))