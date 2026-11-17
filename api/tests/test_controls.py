"""Controls against the fake Moonraker: what is sent, what is refused, and the action log."""

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.drivers.moonraker import gcode_allowed
from app.main import create_app

from .conftest import wait_for_sync


@pytest.fixture
def client(tmp_path, printers_file, monkeypatch):
    import app.controls as controls

    monkeypatch.setattr(controls, "MIN_GAP", 0.0)
    s = Settings(data_dir=tmp_path / "data", printers_file=printers_file, reconnect_seconds=0.3,
                 static_dir=tmp_path / "none", controls_enabled=True)
    with TestClient(create_app(s)) as c:
        wait_for_sync(lambda: c.get("/api/printers/k1c").json()["state"] == "printing")
        yield c


def status(c):
    return c.get("/api/printers/k1c").json()


def post(c, action, body=None):
    return c.post(f"/api/printers/k1c/{action}", json=body)


def test_config_reports_controls_on(client):
    assert client.get("/api/config").json()["controls_enabled"] is True


def test_pause_resume_cancel(client, fake_server):
    r = post(client, "pause")
    assert r.status_code == 200 and r.json()["command"] == "printer.print.pause"
    wait_for_sync(lambda: status(client)["state"] == "paused")
    assert post(client, "pause").status_code == 409  # not printing any more
    assert post(client, "resume").status_code == 200
    wait_for_sync(lambda: status(client)["state"] == "printing")
    assert post(client, "cancel").status_code == 200
    wait_for_sync(lambda: status(client)["state"] == "cancelled")
    assert post(client, "resume").status_code == 409
    assert post(client, "cancel").status_code == 409


def test_falls_back_to_macros_when_print_methods_are_missing(client, fake_server):
    fake_server.fake.print_methods = False
    r = post(client, "pause")
    assert r.status_code == 200 and r.json()["command"] == "PAUSE"
    wait_for_sync(lambda: status(client)["state"] == "paused")
    assert fake_server.fake.scripts == ["PAUSE"]


def test_speed_slider(client, fake_server):
    assert post(client, "speed", {"percent": 70}).json()["command"] == "M220 S70"
    wait_for_sync(lambda: status(client)["speed_percent"] == 70)
    for bad in (40, 75, 170):
        assert post(client, "speed", {"percent": bad}).status_code == 422
    assert fake_server.fake.scripts == ["M220 S70"]


def test_fans_temps_light(client, fake_server):
    assert post(client, "fan", {"name": "Auxiliary fan", "percent": 60}).json()["command"] == \
        "SET_FAN_SPEED FAN=side_fan SPEED=0.6"
    assert post(client, "fan", {"name": "Model fan", "percent": 0}).json()["command"] == \
        "SET_FAN_SPEED FAN=model_fan SPEED=0"
    assert post(client, "fan", {"name": "Case fan", "percent": 100}).json()["command"] == \
        "SET_FAN_SPEED FAN=chassis_fan SPEED=1"
    assert post(client, "temp", {"heater": "nozzle", "target": 210}).json()["command"] == "M104 S210"
    assert post(client, "temp", {"heater": "bed", "target": 0}).json()["command"] == "M140 S0"
    assert post(client, "light", {"on": False}).json()["command"] == "SET_PIN PIN=LED VALUE=0"
    wait_for_sync(lambda: status(client)["light_on"] is False and status(client)["nozzle"]["target"] == 210)
    fans = {f["name"]: f["percent"] for f in status(client)["fans"]}
    assert fans["Auxiliary fan"] == 60 and fans["Case fan"] == 100

    assert post(client, "fan", {"name": "Model fan", "percent": 55}).status_code == 422
    assert post(client, "fan", {"name": "Nope", "percent": 50}).status_code == 404
    assert post(client, "temp", {"heater": "nozzle", "target": 301}).status_code == 422
    assert post(client, "temp", {"heater": "nozzle", "target": 20}).status_code == 422
    assert post(client, "temp", {"heater": "bed", "target": 101}).status_code == 422
    assert post(client, "temp", {"heater": "chamber", "target": 40}).status_code == 404
    # Only what was asked for reached the printer.
    assert fake_server.fake.scripts == [
        "SET_FAN_SPEED FAN=side_fan SPEED=0.6", "SET_FAN_SPEED FAN=model_fan SPEED=0",
        "SET_FAN_SPEED FAN=chassis_fan SPEED=1", "M104 S210", "M140 S0", "SET_PIN PIN=LED VALUE=0"]


def test_speed_only_during_a_print(client, fake_server):
    fake_server.scenario("idle")
    wait_for_sync(lambda: status(client)["state"] == "idle")
    assert post(client, "speed", {"percent": 100}).status_code == 409
    assert post(client, "temp", {"heater": "nozzle", "target": 50}).status_code == 200  # heating while idle is allowed


def test_nothing_sent_while_offline(client, fake_server):
    fake_server.offline(30)
    wait_for_sync(lambda: status(client)["state"] == "offline")
    assert post(client, "light", {"on": True}).status_code == 409
    assert fake_server.fake.scripts == []


def test_rate_limited(tmp_path, printers_file, fake_server):
    s = Settings(data_dir=tmp_path / "data", printers_file=printers_file, reconnect_seconds=0.3,
                 static_dir=tmp_path / "none", controls_enabled=True)
    with TestClient(create_app(s)) as c:
        wait_for_sync(lambda: status(c)["state"] == "printing")
        assert post(c, "light", {"on": True}).status_code == 200
        assert post(c, "light", {"on": False}).status_code == 429
        assert fake_server.fake.scripts == ["SET_PIN PIN=LED VALUE=1"]


def test_action_log(client, fake_server):
    post(client, "light", {"on": True})
    post(client, "speed", {"percent": 999})
    rows = client.get("/api/printers/k1c/actions").json()
    assert [(r["action"], r["result"]) for r in rows] == [("speed", "refused"), ("light", "sent")]
    assert rows[1]["command"] == "SET_PIN PIN=LED VALUE=1" and rows[1]["device"]
    assert "50 to 160" in rows[0]["detail"]


def test_gcode_allowlist():
    for ok in ("M220 S50", "M220 S160", "M104 S0", "M104 S300", "M140 S100", "PAUSE",
               "SET_FAN_SPEED FAN=model_fan SPEED=0.4", "SET_PIN PIN=LED VALUE=1"):
        assert gcode_allowed(ok), ok
    for bad in ("M220 S170", "M220 S105", "M104 S301", "M140 S101", "G28", "M112", "PAUSE\nG28",
                "SET_PIN PIN=LED VALUE=1 G28", "SET_FAN_SPEED FAN=model_fan SPEED=0.45", "FIRMWARE_RESTART"):
        assert not gcode_allowed(bad), bad
