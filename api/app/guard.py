"""Request guard: only the app's own page may change things.

The app has no login: it is meant for a private network (LAN, Tailscale). That
alone doesn't stop *another web page* open on one of your devices from talking
to it, because a browser will send a request to any address a page asks for:

- Cross-site requests. A page on some other site can POST to
  http://<app>/api/printers/k1c/cancel. The browser won't show that page the
  answer, but the request still lands and the print stops. Same for deleting a
  file, starting a print, or opening the camera WebSocket.
- DNS rebinding. A site points its own name at the app's address, so its
  requests look same-origin to the browser.

So, for everything under /api (except the camera relay's own callback, which
go2rtc calls with a secret token):

1. The Host header must be an IP address, `localhost`, or a name listed in
   ALLOWED_HOSTS. That stops rebinding, where the Host is the attacker's name.
2. Anything that changes state (POST, PUT, PATCH, DELETE) and every WebSocket
   must come from the app's own page: an Origin header, when there is one,
   must match the Host; `Sec-Fetch-Site`, when there is one, must be
   same-origin (or none: typed by the user). Browsers always send at least one
   of these on such requests. Requests from scripts and curl send neither and
   are let through, since they aren't a web page acting behind your back.

GET requests don't change anything, and a cross-site page can't read their
answers, so they only get the Host check.
"""

from __future__ import annotations

import ipaddress
import logging
from urllib.parse import urlsplit

from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

log = logging.getLogger(__name__)

UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}
EXEMPT_PREFIXES = ("/api/internal/",)  # go2rtc's handshake callback: server to server, token-checked


def parse_hosts(value: str) -> set[str]:
    """ALLOWED_HOSTS: comma or space separated names, without ports."""
    return {h.strip().lower().rstrip(".") for h in value.replace(",", " ").split() if h.strip()}


def _hostname(host: str) -> str:
    """'name:8090' -> 'name', '[::1]:8090' -> '::1'."""
    host = host.strip().lower()
    if host.startswith("["):
        return host[1:].split("]", 1)[0]
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


def host_allowed(host: str, allowed: set[str]) -> bool:
    name = _hostname(host).rstrip(".")
    if not name:
        return False
    if "*" in allowed or name in allowed:
        return True
    if name == "localhost" or name.endswith(".localhost"):
        return True
    try:
        ipaddress.ip_address(name)
        return True
    except ValueError:
        return False


def same_origin(origin: str, host: str) -> bool:
    """Origin 'http://100.94.19.11:8090' matches Host '100.94.19.11:8090'."""
    if not origin or origin == "null":
        return False
    parts = urlsplit(origin)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return False
    netloc = parts.netloc.lower()
    host = host.strip().lower()
    default = {"http": ":80", "https": ":443"}[parts.scheme]
    strip = lambda h: h[: -len(default)] if h.endswith(default) else h  # noqa: E731
    return strip(netloc) == strip(host)


def refusal(scope: Scope, allowed: set[str]) -> str | None:
    """Why this request is refused, or None to let it through."""
    path = scope.get("path", "")
    if not path.startswith("/api/") or path.startswith(EXEMPT_PREFIXES):
        return None
    headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
    host = headers.get("host", "")
    if not host_allowed(host, allowed):
        return f"unknown host name {_hostname(host)!r}: open the app by its IP address, or add the name to ALLOWED_HOSTS"
    if scope["type"] == "http" and scope.get("method", "GET").upper() not in UNSAFE:
        return None
    origin = headers.get("origin")
    if origin is not None and not same_origin(origin, host):
        return "this request came from another web page"
    site = headers.get("sec-fetch-site")
    if site is not None and site not in ("same-origin", "none"):
        return "this request came from another web page"
    return None


class RequestGuard:
    """ASGI middleware applying `refusal` to HTTP requests and WebSockets."""

    def __init__(self, app: ASGIApp, allowed_hosts: set[str] | None = None):
        self.app = app
        self.allowed = allowed_hosts or set()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            reason = refusal(scope, self.allowed)
            if reason:
                client = scope.get("client") or ("?", 0)
                log.warning("refused %s %s from %s: %s", scope.get("method", "WS"), scope.get("path"), client[0], reason)
                if scope["type"] == "websocket":
                    await receive()  # websocket.connect
                    await send({"type": "websocket.close", "code": 1008})
                    return
                await PlainTextResponse(reason, status_code=403)(scope, receive, send)
                return
        await self.app(scope, receive, send)
