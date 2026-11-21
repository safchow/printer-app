"""The request guard: other web pages can't change anything or open WebSockets."""

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.config import Settings
from app.guard import host_allowed, parse_hosts, same_origin
from app.main import create_app

from .conftest import wait_for_sync

APP = "http://testserver"
EVIL = "https://evil.example"


@pytest.fixture
def client(tmp_path, printers_file, monkeypatch):
    import app.controls as controls

    monkeypatch.setattr(controls, "MIN_GAP", 0.0)
    s = Settings(data_dir=tmp_path / "data", printers_file=printers_file, reconnect_seconds=0.3,
                 static_dir=tmp_path / "none", controls_enabled=True)
    with TestClient(create_app(s)) as c:
        wait_for_sync(lambda: c.get("/api/printers/k1c").json()["state"] == "printing")
        yield c


def state(c):
    return c.get("/api/printers/k1c").json()["state"]


def test_cross_site_cancel_is_refused(client):
    r = client.post("/api/printers/k1c/cancel", headers={"Origin": EVIL})
    assert r.status_code == 403
    assert state(client) == "printing"
    # and it shows up nowhere as a press
    assert all(a["action"] != "cancel" for a in client.get("/api/printers/k1c/actions").json())


@pytest.mark.parametrize("headers", [
    {"Origin": "null"},
    {"Sec-Fetch-Site": "cross-site"},
    {"Sec-Fetch-Site": "same-site"},
    {"Origin": "http://testserver:9999"},
    {"Origin": APP, "Sec-Fetch-Site": "cross-site"},
])
def test_other_pages_are_refused(client, headers):
    assert client.post("/api/printers/k1c/pause", headers=headers).status_code == 403
    assert client.delete("/api/files/1", headers=headers).status_code == 403


def test_own_page_and_scripts_work(client):
    assert client.post("/api/printers/k1c/pause",
                       headers={"Origin": APP, "Sec-Fetch-Site": "same-origin"}).status_code == 200
    wait_for_sync(lambda: state(client) == "paused")
    assert client.post("/api/printers/k1c/resume").status_code == 200  # curl: no Origin, no Sec-Fetch-Site


def test_reads_from_other_pages_still_work(client):
    # A cross-site page can't read the answer anyway; GETs change nothing.
    assert client.get("/api/printers", headers={"Origin": EVIL, "Sec-Fetch-Site": "cross-site"}).status_code == 200


def test_unknown_host_name_is_refused(client):
    # DNS rebinding: the attacker's name pointed at the app. Origin matches Host, so only the Host check stops it.
    h = {"Host": "rebind.evil.example", "Origin": "http://rebind.evil.example"}
    assert client.post("/api/printers/k1c/cancel", headers=h).status_code == 403
    assert client.get("/api/printers", headers={"Host": "rebind.evil.example"}).status_code == 403
    assert client.get("/api/printers", headers={"Host": "100.94.19.11:8090"}).status_code == 200


def test_cross_site_websocket_is_refused(client):
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/ws", headers={"Origin": EVIL}) as ws:
            ws.receive_json()
    with client.websocket_connect("/api/ws", headers={"Origin": APP}) as ws:
        assert ws.receive_json()["type"] == "snapshot"


def test_camera_callback_is_exempt(client):
    # go2rtc calls back server to server; the per-boot token guards it, not the Origin.
    r = client.post("/api/internal/camera/wrong-token/k1c", headers={"Host": "printer-api:8090"}, content=b"x")
    assert r.status_code != 403


def test_helpers():
    allowed = parse_hosts("MyServer, myserver.tail1234.ts.net.")
    assert host_allowed("myserver:8090", allowed)
    assert host_allowed("myserver.tail1234.ts.net", allowed)
    assert host_allowed("100.94.19.11:8090", set())
    assert host_allowed("[::1]:8090", set())
    assert host_allowed("localhost:5173", set())
    assert not host_allowed("evil.example", allowed)
    assert not host_allowed("", set())
    assert host_allowed("anything", {"*"})
    assert same_origin("http://100.94.19.11:8090", "100.94.19.11:8090")
    assert same_origin("http://a", "a:80")
    assert not same_origin("https://100.94.19.11:8090", "100.94.19.12:8090")
    assert not same_origin("null", "a")
