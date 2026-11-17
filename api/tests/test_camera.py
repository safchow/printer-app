"""Camera handshake relay, against a fake Creality camera."""

import base64
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.camera import CameraError, CameraRelay, SessionDescription, decode_answer, encode_offer
from app.config import PrinterConfig, Settings
from app.main import create_app
from fakes.fake_camera import FAKE_ANSWER_SDP, FakeCamera

from .conftest import wait_for_sync

OFFER = SessionDescription(type="offer", sdp="v=0\r\nfake offer\r\n")


def printer(camera="creality_webrtc") -> PrinterConfig:
    return PrinterConfig(id="k1c", name="K1C", host="10.0.0.9", camera=camera)


def relay_for(cam: FakeCamera, **kw) -> CameraRelay:
    return CameraRelay(transport=httpx.ASGITransport(app=cam.app()), **kw)


def test_encoding_round_trip():
    body = encode_offer(OFFER)
    assert json.loads(base64.b64decode(body)) == {"type": "offer", "sdp": OFFER.sdp}
    answer = json.dumps({"type": "answer", "sdp": "x"})
    assert decode_answer(base64.b64encode(answer.encode()).decode()).sdp == "x"
    assert decode_answer(answer).sdp == "x"  # plain JSON also accepted
    with pytest.raises(CameraError):
        decode_answer("not an answer")
    with pytest.raises(CameraError):
        decode_answer(json.dumps({"type": "offer", "sdp": "x"}))


async def test_relays_offer_and_answer():
    cam = FakeCamera()
    answer = await relay_for(cam).offer(printer(), OFFER)
    assert answer.type == "answer" and answer.sdp == FAKE_ANSWER_SDP
    assert cam.offers == [{"type": "offer", "sdp": OFFER.sdp}]


async def test_plain_json_answer():
    answer = await relay_for(FakeCamera(raw_json=True)).offer(printer(), OFFER)
    assert answer.sdp == FAKE_ANSWER_SDP


async def test_rate_limited_per_printer():
    cam = FakeCamera()
    relay = relay_for(cam, min_interval=60)
    await relay.offer(printer(), OFFER)
    with pytest.raises(CameraError) as err:
        await relay.offer(printer(), OFFER)
    assert err.value.status == 429
    assert len(cam.offers) == 1  # the second ask never reached the camera


async def test_camera_errors():
    with pytest.raises(CameraError) as err:
        await relay_for(FakeCamera()).offer(printer("none"), OFFER)
    assert err.value.status == 404
    with pytest.raises(CameraError) as err:
        await relay_for(FakeCamera()).offer(printer("mjpeg"), OFFER)
    assert err.value.status == 501
    with pytest.raises(CameraError) as err:
        await relay_for(FakeCamera(status=500)).offer(printer(), OFFER)
    assert err.value.status == 502


async def test_unreachable_camera():
    from .conftest import free_port

    cfg = PrinterConfig(id="k1c", name="K1C", host="127.0.0.1", camera="creality_webrtc", camera_port=free_port())
    with pytest.raises(CameraError) as err:
        await CameraRelay(timeout=2).offer(cfg, OFFER)
    assert err.value.status == 502


def test_offer_endpoint(tmp_path, printers_file, fake_server, monkeypatch):
    cam = FakeCamera()
    s = Settings(data_dir=tmp_path / "d", printers_file=printers_file, reconnect_seconds=0.3,
                 camera_min_interval=0, static_dir=tmp_path / "none")
    app = create_app(s)
    with TestClient(app) as c:
        app.state.camera.transport = httpx.ASGITransport(app=cam.app())
        wait_for_sync(lambda: c.get("/api/printers/k1c").json()["state"] == "printing")
        assert c.get("/api/printers/k1c").json()["camera"] == "creality_webrtc"
        r = c.post("/api/printers/k1c/camera/offer", json={"type": "offer", "sdp": "v=0"})
        assert r.status_code == 200 and r.json()["type"] == "answer"
        assert c.post("/api/printers/nope/camera/offer", json={"type": "offer", "sdp": "v=0"}).status_code == 404
        assert c.post("/api/printers/k1c/camera/offer", json={"type": "offer", "sdp": "x" * 70000}).status_code == 422
        # The printer's Moonraker connection is untouched by camera calls.
        assert set(fake_server.fake.methods) == {"printer.objects.subscribe"}


def test_offer_refused_while_offline(tmp_path, printers_file, fake_server):
    s = Settings(data_dir=tmp_path / "d", printers_file=printers_file, reconnect_seconds=0.3,
                 static_dir=tmp_path / "none")
    app = create_app(s)
    with TestClient(app) as c:
        wait_for_sync(lambda: c.get("/api/printers/k1c").json()["state"] == "printing")
        fake_server.offline(30)
        wait_for_sync(lambda: c.get("/api/printers/k1c").json()["state"] == "offline")
        assert c.post("/api/printers/k1c/camera/offer", json={"type": "offer", "sdp": "v=0"}).status_code == 409
