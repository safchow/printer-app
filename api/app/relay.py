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
import logging
import secrets
import time
from pathlib import Path
from urllib.parse import quote

from .camera import CameraError, SessionDescription

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


def fix_answer(answer: str, offer: str) -> str:
    """Make the K1C's answer acceptable to a receive-only client.

    * Our side only receives, so a sendrecv answer becomes sendonly.
    * The K1C has been seen sending RTP with an H.264 payload type that was in
      the offer but missing from its answer (go2rtc issue #2024). Any offered
      H.264 payload type the answer leaves out is added back, after the
      answer's own, so the client knows how to read those packets.
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
        s[0] = " ".join(m + missing)
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

    When the viewer goes, the upstream stays open for `linger` seconds (its
    messages are dropped) so go2rtc keeps the printer stream running for the
    next page.
    """
    from starlette.websockets import WebSocketDisconnect

    linger = LINGER if linger is None else linger

    async def down() -> None:
        async for message in upstream:
            if isinstance(message, bytes):
                await client.send_bytes(message)
            else:
                await client.send_text(message)

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
