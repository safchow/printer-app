"""Camera relay: SDP fixes, go2rtc's handshake callback and the viewer WebSocket proxy."""

import asyncio
import base64
import json
import threading

import httpx
import pytest
import websockets
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.relay import Go2rtc, decode_go2rtc_offer, fix_answer, fix_offer
from fakes.fake_camera import FakeCamera

from .conftest import free_port, wait_for_sync

# Shaped like go2rtc's Creality offer: receive-only video, two H.264 payload types.
OFFER = (
    "v=0\r\no=- 1 1 IN IP4 0.0.0.0\r\ns=-\r\nt=0 0\r\n"
    "m=video 9 UDP/TLS/RTP/SAVPF 96 98 102\r\nc=IN IP4 0.0.0.0\r\na=mid:0\r\na=recvonly\r\n"
    "a=rtpmap:96 H264/90000\r\na=fmtp:96 profile-level-id=42001f;packetization-mode=1\r\n"
    "a=rtpmap:98 H264/90000\r\na=fmtp:98 profile-level-id=42e01f;packetization-mode=1\r\n"
    "a=rtcp-fb:98 nack\r\na=rtpmap:102 VP8/90000\r\n"
)
# Shaped like the K1C's answer: sendrecv, and it leaves 98 out.
ANSWER = (
    "v=0\r\no=- 2 2 IN IP4 10.0.0.9\r\ns=-\r\nt=0 0\r\n"
    "m=video 9 UDP/TLS/RTP/SAVPF 0 96\r\nc=IN IP4 0.0.0.0\r\na=mid:0\r\na=sendrecv\r\n"
    "a=rtpmap:96 H264/90000\r\n"
)


def test_fix_offer_makes_video_sendrecv():
    fixed = fix_offer(OFFER)
    assert "a=sendrecv" in fixed and "a=recvonly" not in fixed
    assert fixed.endswith("\r\n") and "\r\n\r\n" not in fixed


def test_fix_answer_adds_missing_h264_and_flips_direction():
    fixed = fix_answer(ANSWER, fix_offer(OFFER))
    lines = fixed.split("\r\n")
    m = next(l for l in lines if l.startswith("m=video"))
    assert m.endswith(" 0 96 98")  # the answer's own first; VP8 not added
    assert "a=rtpmap:98 H264/90000" in lines
    assert "a=fmtp:98 profile-level-id=42e01f;packetization-mode=1" in lines
    assert "a=rtcp-fb:98 nack" in lines
    assert "a=sendonly" in lines and "a=sendrecv" not in lines


def test_fix_answer_leaves_complete_answer_alone():
    full = ANSWER.replace(" 0 96\r\n", " 96 98\r\n")
    assert fix_answer(full, OFFER).count("a=rtpmap:98") == 0  # 98 listed already: nothing copied


def test_decode_go2rtc_offer():
    body = base64.b64encode(json.dumps({"type": "offer", "sdp": OFFER}).encode())
    assert decode_go2rtc_offer(body).sdp == OFFER
    with pytest.raises(Exception):
        decode_go2rtc_offer(b"nope")


def test_ws_url_names_the_stream():
    g = Go2rtc("http://go2rtc:1984/", "http://printer-api:8090")
    url = g.ws_url("k1c")
    assert url.startswith("ws://go2rtc:1984/api/ws?name=k1c&src=webrtc%3Ahttp%3A%2F%2Fprinter-api%3A8090")
    assert g.token in url and "%23format%3Dcreality" in url


def make_app(tmp_path, printers_file, go2rtc_url="http://go2rtc.invalid:1984"):
    s = Settings(data_dir=tmp_path / "d", printers_file=printers_file, reconnect_seconds=0.3,
                 camera_min_interval=0, static_dir=tmp_path / "none", go2rtc_url=go2rtc_url)
    return create_app(s)


def test_handshake_callback(tmp_path, printers_file, fake_server):
    cam = FakeCamera()
    app = make_app(tmp_path, printers_file)
    with TestClient(app) as c:
        app.state.camera.transport = httpx.ASGITransport(app=cam.app())
        wait_for_sync(lambda: c.get("/api/printers/k1c").json()["state"] == "printing")
        assert c.get("/api/config").json()["camera_mode"] == "relay"
        token = app.state.go2rtc.token
        body = base64.b64encode(json.dumps({"type": "offer", "sdp": OFFER}).encode())

        assert c.post(f"/api/internal/camera/wrong/k1c", content=body).status_code == 404
        assert cam.offers == []  # a bad token never reaches the camera

        r = c.post(f"/api/internal/camera/{token}/k1c", content=body)
        assert r.status_code == 200
        answer = json.loads(base64.b64decode(r.text))
        assert answer["type"] == "answer" and "a=sendonly" in answer["sdp"]
        assert "a=sendrecv" in cam.offers[0]["sdp"]  # the printer got a sendrecv offer
        assert (tmp_path / "d" / "camera-handshake-k1c.txt").exists()

        assert c.post(f"/api/internal/camera/{token}/k1c", content=b"junk").status_code == 400
        assert c.post(f"/api/internal/camera/{token}/nope", content=body).status_code == 404
        assert set(fake_server.fake.methods) == {"printer.objects.subscribe"}


def test_handshake_callback_off_without_relay(tmp_path, printers_file, fake_server):
    app = make_app(tmp_path, printers_file, go2rtc_url="")
    with TestClient(app) as c:
        assert c.get("/api/config").json()["camera_mode"] == "direct"
        assert c.post("/api/internal/camera/x/k1c", content=b"x").status_code == 404
        with c.websocket_connect("/api/printers/k1c/camera/ws") as ws:
            assert ws.receive_json()["type"] == "error"


class FakeGo2rtc:
    """A stand-in for go2rtc's /api/ws: answers an mse request with a mime type and two segments."""

    def __init__(self):
        self.port = free_port()
        self.paths: list[str] = []
        self.closed = threading.Event()
        self.loop = asyncio.new_event_loop()
        self.ready = threading.Event()
        threading.Thread(target=self._run, daemon=True).start()
        self.ready.wait(5)

    async def handler(self, ws):
        self.paths.append(ws.request.path)
        msg = json.loads(await ws.recv())
        assert msg["type"] == "mse"
        await ws.send(json.dumps({"type": "mse", "value": 'video/mp4; codecs="avc1.640029"'}))
        await ws.send(b"\x00init")
        await ws.send(b"\x00segment")
        try:
            await ws.wait_closed()
        finally:
            self.closed.set()

    def _run(self):
        async def main():
            async with websockets.serve(self.handler, "127.0.0.1", self.port):
                self.ready.set()
                await asyncio.Future()
        self.loop.run_until_complete(main())


def test_viewer_ws_is_proxied_to_go2rtc(tmp_path, printers_file, fake_server, monkeypatch):
    import app.relay as relay

    monkeypatch.setattr(relay, "LINGER", 0.0)
    fake = FakeGo2rtc()
    app = make_app(tmp_path, printers_file, go2rtc_url=f"http://127.0.0.1:{fake.port}")
    with TestClient(app) as c:
        wait_for_sync(lambda: c.get("/api/printers/k1c").json()["state"] == "printing")
        with c.websocket_connect("/api/printers/k1c/camera/ws") as ws:
            ws.send_text(json.dumps({"type": "mse", "value": "avc1.640029"}))
            assert json.loads(ws.receive_text())["type"] == "mse"
            assert ws.receive_bytes() == b"\x00init"
            assert ws.receive_bytes() == b"\x00segment"
        assert fake.paths and fake.paths[0].startswith("/api/ws?name=k1c&src=webrtc")
        with c.websocket_connect("/api/printers/nope/camera/ws") as ws:
            assert ws.receive_json() == {"type": "error", "value": "no such printer"}


def test_viewer_ws_reports_missing_relay(tmp_path, printers_file, fake_server):
    app = make_app(tmp_path, printers_file, go2rtc_url=f"http://127.0.0.1:{free_port()}")
    with TestClient(app) as c:
        wait_for_sync(lambda: c.get("/api/printers/k1c").json()["state"] == "printing")
        with c.websocket_connect("/api/printers/k1c/camera/ws") as ws:
            assert ws.receive_json() == {"type": "error", "value": "the camera relay isn't running"}


def test_upstream_lingers_after_viewer_leaves(tmp_path, printers_file, fake_server, monkeypatch):
    import time

    import app.relay as relay

    monkeypatch.setattr(relay, "LINGER", 1.0)
    fake = FakeGo2rtc()
    app = make_app(tmp_path, printers_file, go2rtc_url=f"http://127.0.0.1:{fake.port}")
    with TestClient(app) as c:
        wait_for_sync(lambda: c.get("/api/printers/k1c").json()["state"] == "printing")
        with c.websocket_connect("/api/printers/k1c/camera/ws") as ws:
            ws.send_text(json.dumps({"type": "mse", "value": "avc1.640029"}))
            ws.receive_text()
        time.sleep(0.3)
        assert not fake.closed.is_set()  # go2rtc still has a viewer, so the stream keeps running
        assert fake.closed.wait(3)  # and it is let go after the linger
