"""A fake Creality camera handshake endpoint for tests and local development.

Answers POST /call/webrtc_local the way the K1C does (base64 JSON in, base64
JSON out), but the answer is a placeholder: no video will actually flow.
"""

from __future__ import annotations

import base64
import json

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route

FAKE_ANSWER_SDP = (
    "v=0\r\no=- 0 0 IN IP4 127.0.0.1\r\ns=fake-k1c-camera\r\nt=0 0\r\n"
    "m=video 9 UDP/TLS/RTP/SAVPF 96\r\nc=IN IP4 0.0.0.0\r\na=sendonly\r\na=mid:0\r\n"
    "a=ice-ufrag:fake\r\na=ice-pwd:fakefakefakefakefakefake\r\n"
    "a=fingerprint:sha-256 " + ":".join(["AB"] * 32) + "\r\n"
    "a=setup:active\r\na=rtcp-mux\r\na=rtpmap:96 H264/90000\r\n"
)


class FakeCamera:
    def __init__(self, status: int = 200, raw_json: bool = False):
        self.status = status
        self.raw_json = raw_json  # answer with plain JSON instead of base64
        self.offers: list[dict] = []

    def app(self) -> Starlette:
        cam = self

        async def call(request: Request):
            body = (await request.body()).decode()
            try:
                offer = json.loads(base64.b64decode(body))
            except Exception:
                return PlainTextResponse("bad offer", status_code=400)
            cam.offers.append(offer)
            if cam.status != 200:
                return PlainTextResponse("error", status_code=cam.status)
            answer = json.dumps({"type": "answer", "sdp": FAKE_ANSWER_SDP})
            return PlainTextResponse(answer if cam.raw_json else base64.b64encode(answer.encode()).decode())

        return Starlette(routes=[Route("/call/webrtc_local", call, methods=["POST"])])
