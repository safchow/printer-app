"""Camera relay: the server pulls one stream from the printer and passes it on to every viewer.

go2rtc (a sidecar container, see compose.yaml and docs/camera-relay.md) does the
media work: it holds one WebRTC connection to the printer and serves the video
to browsers as fragmented MP4 over a WebSocket (MSE). Nothing is re-encoded.

This module is the glue:

* go2rtc's Creality source posts its offer to *this app* (a private callback URL
  with a per-boot token), not to the printer. We fix the offer and the answer
  the way the K1C needs, and send the offer on through the same rate-limited
  handshake the browser path uses (camera.CameraRelay).
* Browsers never talk to go2rtc directly: /api/printers/{id}/camera/ws is
  proxied to go2rtc's WebSocket, so only the app's own port is exposed.

go2rtc only connects to the printer while someone is watching. The proxy keeps
its upstream open for LINGER seconds after a viewer leaves, so moving between
the dashboard and a printer page reuses the running stream instead of making a
new handshake.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import re
import logging
import secrets
import time
from pathlib import Path
from urllib.parse import quote

import httpx
import websockets

from .camera import CameraError, SessionDescription
from .fmp4 import StreamFixer

log = logging.getLogger(__name__)

LINGER = 20.0


# --- SDP fixes ---------------------------------------------------------------

def _sections(sdp: str) -> list[list[str]]:
    """Split an SDP into [session lines, media section lines...]."""
    lines = [l for l in sdp.replace("\r\n", "\n").split("\n") if l]
    out: list[list[str]] = [[]]
    for line in lines:
        if line.startswith("m="):
            out.append([])
        out[-1].append(line)
    return out


def _join(sections: list[list[str]]) -> str:
    return "\r\n".join(l for s in sections for l in s) + "\r\n"


def _codec_lines(section: list[str]) -> dict[str, list[str]]:
    """Payload type -> its a=rtpmap / a=fmtp / a=rtcp-fb lines."""
    out: dict[str, list[str]] = {}
    for line in section:
        for prefix in ("a=rtpmap:", "a=fmtp:", "a=rtcp-fb:"):
            if line.startswith(prefix):
                pt = line[len(prefix):].split(" ", 1)[0]
                out.setdefault(pt, []).append(line)
    return out


def _rtpmap(section: list[str], pt: str) -> str:
    for line in section:
        if line.startswith(f"a=rtpmap:{pt} "):
            return line.split(" ", 1)[1]
    return ""


DIRECTIONS = ("a=sendrecv", "a=sendonly", "a=recvonly", "a=inactive")


def fix_offer(sdp: str) -> str:
    """The K1C only sends video when the offer is sendrecv (as its own page does)."""
    sections = _sections(sdp)
    for s in sections[1:]:
        if s[0].startswith("m=video"):
            s[:] = ["a=sendrecv" if l in DIRECTIONS else l for l in s]
    return _join(sections)


def _profile(section: list[str], pt: str) -> str | None:
    for line in section:
        if line.startswith(f"a=fmtp:{pt} "):
            m = re.search(r"profile-level-id=([0-9a-fA-F]{6})", line)
            if m:
                return m.group(1).lower()
    return None


def fix_answer(answer: str, offer: str) -> str:
    """Make the K1C's answer acceptable to a receive-only client.

    * Our side only receives, so a sendrecv answer becomes sendonly.
    * The K1C sends RTP with an H.264 payload type that was in the offer but
      is missing from its answer (go2rtc issue #2024): seen live, its answer
      lists "96 0" while the video arrives on another offered type. Any offered
      H.264 payload type the answer leaves out is added back, so the client can
      read those packets.
    * Where they go matters: go2rtc's Creality fix drops the answer's first
      format (a placeholder) and binds viewers to the next one. With the added
      types after the answer's own, viewers sat on a type that never carries
      video while the real video went to a second receiver nobody read (seen
      2026-11-19: the browser got only the init segment and frame requests
      hung). So the added types go straight after the first format, those
      whose H.264 profile matches the answer's own first: the K1C streams the
      profile it announces.
    """
    a_sections = _sections(answer)
    o_video = next((s for s in _sections(offer)[1:] if s[0].startswith("m=video")), None)
    for s in a_sections[1:]:
        if not s[0].startswith("m=video"):
            continue
        s[:] = ["a=sendonly" if l in ("a=sendrecv",) else l for l in s]
        if o_video is None:
            continue
        m = s[0].split(" ")
        have = set(m[3:])
        offered = _codec_lines(o_video)
        missing = [pt for pt in o_video[0].split(" ")[3:]
                   if pt not in have and _rtpmap(o_video, pt).upper().startswith("H264/")]
        if not missing:
            continue
        announced = {p for p in (_profile(s, pt) for pt in m[3:]) if p}
        missing.sort(key=lambda pt: 0 if _profile(o_video, pt) in announced else 1)  # stable
        s[0] = " ".join(m[:4] + missing + m[4:])
        s.extend(line for pt in missing for line in offered.get(pt, []))
    return _join(a_sections)


# --- go2rtc's Creality handshake body ------------------------------------------

def decode_go2rtc_offer(body: bytes) -> SessionDescription:
    try:
        data = json.loads(base64.b64decode(body, validate=True))
        return SessionDescription(type=data["type"], sdp=data["sdp"])
    except Exception as exc:  # noqa: BLE001 - any malformed body is the same error
        raise CameraError(400, "expected base64(JSON offer)") from exc


def encode_answer(answer: SessionDescription) -> str:
    return base64.b64encode(json.dumps(answer.model_dump()).encode()).decode()


# --- go2rtc client -------------------------------------------------------------

class Go2rtc:
    """Where go2rtc is, and how it reaches back to this app."""

    def __init__(self, url: str, callback_url: str, debug_dir: Path | None = None):
        self.url = url.rstrip("/")
        self.callback_url = callback_url.rstrip("/")
        self.token = secrets.token_urlsafe(24)  # new every start; only go2rtc learns it
        self.debug_dir = debug_dir

    def check_token(self, token: str) -> bool:
        return secrets.compare_digest(token, self.token)

    def source(self, printer_id: str) -> str:
        return f"webrtc:{self.callback_url}/api/internal/camera/{self.token}/{printer_id}#format=creality"

    def ws_url(self, printer_id: str) -> str:
        base = self.url.replace("http://", "ws://", 1).replace("https://", "wss://", 1)
        # name= makes go2rtc create the stream on first use, without writing its config file.
        return f"{base}/api/ws?name={quote(printer_id)}&src={quote(self.source(printer_id), safe='')}"

    def save_handshake(self, printer_id: str, offer: str, sent: str, answer: str, fixed: str) -> None:
        """Keep the last handshake per printer, for debugging a camera that shows nothing."""
        if not self.debug_dir:
            return
        with contextlib.suppress(OSError):
            self.debug_dir.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            (self.debug_dir / f"camera-handshake-{printer_id}.txt").write_text(
                f"# {stamp}\n\n## offer from go2rtc\n{offer}\n## offer sent to printer\n{sent}\n"
                f"## printer's answer\n{answer}\n## answer given to go2rtc\n{fixed}\n"
            )


async def pump(client, upstream, linger: float | None = None) -> None:
    """Copy messages both ways between a viewer (Starlette WebSocket) and go2rtc (websockets).

    What go2rtc sends is fixed on the way (app/fmp4.py): real-time timestamps
    and the right codec label, which iPhone Safari needs.

    When the viewer goes, the upstream stays open for `linger` seconds (its
    messages are dropped) so go2rtc keeps the printer stream running for the
    next page.
    """
    from starlette.websockets import WebSocketDisconnect

    linger = LINGER if linger is None else linger

    fixer = StreamFixer()

    async def down() -> None:
        async for message in upstream:
            out = fixer.binary(message) if isinstance(message, bytes) else fixer.text(message)
            for m in out:
                if isinstance(m, bytes):
                    await client.send_bytes(m)
                else:
                    await client.send_text(m)

    async def up() -> None:
        while True:
            message = await client.receive()
            if message["type"] == "websocket.disconnect":
                return
            if message.get("text") is not None:
                await upstream.send(message["text"])
            elif message.get("bytes") is not None:
                await upstream.send(message["bytes"])

    tasks = [asyncio.create_task(down()), asyncio.create_task(up())]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for t in done:
            exc = t.exception()
            if exc and not isinstance(exc, WebSocketDisconnect | RuntimeError):
                log.debug("camera relay: %r", exc)
    finally:
        upstream_done = tasks[0].done()
        for t in tasks:
            t.cancel()
        if linger > 0 and not upstream_done:
            task = asyncio.create_task(_linger(upstream, linger))
            _lingering.add(task)
            task.add_done_callback(_lingering.discard)
        else:
            with contextlib.suppress(Exception):
                await upstream.close()


_lingering: set[asyncio.Task] = set()


async def _linger(upstream, seconds: float) -> None:
    async def drain() -> None:
        async for _ in upstream:
            pass
    with contextlib.suppress(Exception):
        await asyncio.wait_for(drain(), timeout=seconds)
    with contextlib.suppress(Exception):
        await upstream.close()


# --- stills, for browsers that can't play the live video -----------------------

STILL_MAX_AGE = 1.5  # seconds a still is shared between pages before a new one is asked for
STILL_TIMEOUT = 10.0
FIRST_STILL_TIMEOUT = 20.0  # the first one waits for the camera handshake
MIN_JPEG_BYTES = 1000
STILL_CODECS = "avc1.640029,avc1.64002A,avc1.640033,hvc1.1.6.L153.B0"


class Stills:
    """JPEG stills from go2rtc (/api/frame.jpeg), a fallback for pages that can't play video.

    go2rtc only knows a printer's stream while something watches it, so this
    holds one viewer connection of its own, like an open camera page, and lets
    it go LINGER seconds after the last still was asked for. Stills are shared
    for STILL_MAX_AGE seconds, so several phones cost one request.
    """

    def __init__(self, go2rtc: Go2rtc, client: httpx.AsyncClient | None = None):
        self.go2rtc = go2rtc
        self.client = client or httpx.AsyncClient()
        self.cache: dict[str, tuple[float, bytes]] = {}
        self.locks: dict[str, asyncio.Lock] = {}
        self.viewers: dict[str, asyncio.Task] = {}
        self.wanted: dict[str, float] = {}

    async def get(self, printer_id: str) -> bytes | None:
        self.wanted[printer_id] = time.monotonic()
        lock = self.locks.setdefault(printer_id, asyncio.Lock())
        async with lock:
            hit = self.cache.get(printer_id)
            if hit and time.monotonic() - hit[0] < STILL_MAX_AGE:
                return hit[1]
            fresh = printer_id not in self.viewers or self.viewers[printer_id].done()
            if fresh:
                self.viewers[printer_id] = asyncio.create_task(self._viewer(printer_id), name=f"stills:{printer_id}")
                await asyncio.sleep(0.2)  # let go2rtc create the stream before asking it for a frame
            data = await self._frame(printer_id, FIRST_STILL_TIMEOUT if fresh else STILL_TIMEOUT)
            if data:
                self.cache[printer_id] = (time.monotonic(), data)
            return data

    async def _frame(self, printer_id: str, timeout: float) -> bytes | None:
        try:
            r = await self.client.get(f"{self.go2rtc.url}/api/frame.jpeg", params={"src": printer_id}, timeout=timeout)
        except (httpx.HTTPError, OSError) as exc:
            log.debug("still for %s: %r", printer_id, exc)
            return None
        data = r.content
        if r.status_code != 200 or len(data) < MIN_JPEG_BYTES or not data.startswith(b"\xff\xd8"):
            return None
        return data

    async def _viewer(self, printer_id: str) -> None:
        try:
            async with websockets.connect(self.go2rtc.ws_url(printer_id), max_size=None, open_timeout=5) as ws:
                await ws.send(json.dumps({"type": "mse", "value": STILL_CODECS}))
                drain = asyncio.create_task(self._drain(ws))
                try:
                    while time.monotonic() - self.wanted.get(printer_id, 0) < LINGER and not drain.done():
                        await asyncio.sleep(1)
                finally:
                    drain.cancel()
        except (OSError, websockets.WebSocketException, TimeoutError) as exc:
            log.warning("stills: camera relay unreachable (%r)", exc)

    @staticmethod
    async def _drain(ws) -> None:
        with contextlib.suppress(Exception):
            async for _ in ws:
                pass

    async def stop(self) -> None:
        for task in self.viewers.values():
            task.cancel()
        for task in self.viewers.values():
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        await self.client.aclose()
