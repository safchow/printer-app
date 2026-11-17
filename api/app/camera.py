"""Camera: relays the browser's WebRTC handshake to the printer. Video never passes through here.

Creality's stock firmware serves a WebRTC endpoint on the camera port (8000):
POST /call/webrtc_local with base64(JSON offer) as the body answers with
base64(JSON answer). The browser then gets video straight from the printer
(over the VPN's route to the printer's address).

Rules: one request per user action, at most one handshake per printer every
MIN_INTERVAL seconds, small bodies only, short timeout, no retries.
"""

from __future__ import annotations

import base64
import binascii
import json
import time
from typing import Literal

import httpx
from pydantic import BaseModel, Field

from .config import PrinterConfig

MIN_INTERVAL = 5.0
MAX_SDP = 64 * 1024
TIMEOUT = 10.0


class SessionDescription(BaseModel):
    type: Literal["offer", "answer"]
    sdp: str = Field(max_length=MAX_SDP)


class CameraError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def encode_offer(offer: SessionDescription) -> str:
    return base64.b64encode(json.dumps(offer.model_dump()).encode()).decode()


def decode_answer(body: str) -> SessionDescription:
    text = body.strip()
    for attempt in (lambda t: base64.b64decode(t, validate=True).decode(), lambda t: t):
        try:
            data = json.loads(attempt(text))
        except (binascii.Error, ValueError, UnicodeDecodeError):
            continue
        if isinstance(data, dict) and data.get("type") == "answer" and isinstance(data.get("sdp"), str):
            return SessionDescription(type="answer", sdp=data["sdp"])
    raise CameraError(502, "the camera sent an answer this app doesn't understand")


class CameraRelay:
    def __init__(self, min_interval: float = MIN_INTERVAL, timeout: float = TIMEOUT,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.min_interval = min_interval
        self.timeout = timeout
        self.transport = transport
        self._last: dict[str, float] = {}

    async def offer(self, printer: PrinterConfig, offer: SessionDescription) -> SessionDescription:
        if printer.camera == "none":
            raise CameraError(404, "this printer has no camera configured")
        if printer.camera != "creality_webrtc":
            raise CameraError(501, f"{printer.camera} cameras are not supported yet")
        if offer.type != "offer":
            raise CameraError(422, "expected an offer")

        now = time.monotonic()
        last = self._last.get(printer.id)
        if last is not None and now - last < self.min_interval:
            wait = self.min_interval - (now - last)
            raise CameraError(429, f"camera was just asked; try again in {wait:.0f}s")
        self._last[printer.id] = now

        url = f"http://{printer.host}:{printer.camera_port}/call/webrtc_local"
        try:
            async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
                r = await client.post(url, content=encode_offer(offer),
                                      headers={"Content-Type": "text/plain"})
        except httpx.HTTPError as exc:
            raise CameraError(502, f"camera unreachable ({type(exc).__name__})") from exc
        if r.status_code != 200:
            raise CameraError(502, f"camera answered HTTP {r.status_code}")
        if len(r.content) > 4 * MAX_SDP:
            raise CameraError(502, "camera answer too large")
        return decode_answer(r.text)
