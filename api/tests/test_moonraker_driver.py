"""The driver against the fake Moonraker: status, read-only, one connection, offline + 30 s retry."""

import asyncio

import pytest

from app.config import PrinterConfig
from app.drivers import ControlsDisabled, MoonrakerDriver
from app.drivers.moonraker import READ_ONLY_METHODS, MethodNotAllowed

from .conftest import wait_for


def make_driver(port: int, reconnect: float = 0.3) -> MoonrakerDriver:
    cfg = PrinterConfig(id="k1c", name="K1C", host="127.0.0.1", port=port, camera="creality_webrtc")
    return MoonrakerDriver(cfg, reconnect_seconds=reconnect)


async def test_reads_status_and_updates(fake_server):
    driver = make_driver(fake_server.port)
    seen = []

    async def listener(status):
        seen.append(status)

    driver.on_status(listener)
    driver.start()
    try:
        await wait_for(lambda: driver.status.state == "printing")
        s = driver.status
        assert s.filename == "phone_stand_v3.gcode"
        assert s.total_layers == 240 and s.layer >= 148
        assert s.nozzle.target == 220.0 and s.speed_percent == 100
        assert {f.name for f in s.fans} == {"Model fan", "Auxiliary fan", "Case fan"}
        assert s.light_on is True and s.has_camera
        first = s.progress
        await wait_for(lambda: driver.status.progress > first)  # pushed updates arrive
        assert len(seen) > 1
    finally:
        await driver.stop()


async def test_follows_state_changes(fake_server):
    driver = make_driver(fake_server.port)
    driver.start()
    try:
        await wait_for(lambda: driver.status.state == "printing")
        await asyncio.to_thread(fake_server.scenario, "paused")
        await wait_for(lambda: driver.status.state == "paused")
        await asyncio.to_thread(fake_server.scenario, "idle")
        await wait_for(lambda: driver.status.state == "idle")
        assert driver.status.filename is None
    finally:
        await driver.stop()


async def test_only_read_only_methods_and_one_connection(fake_server):
    driver = make_driver(fake_server.port)
    driver.start()
    try:
        await wait_for(lambda: driver.status.state == "printing")
        await asyncio.sleep(0.3)
        assert set(fake_server.fake.methods) <= READ_ONLY_METHODS
        assert fake_server.fake.max_concurrent == 1
        assert fake_server.fake.http_requests == []  # the driver never makes HTTP calls
    finally:
        await driver.stop()


async def test_refuses_non_read_only_methods():
    driver = make_driver(1)

    class NoSocket:
        async def send(self, _):
            raise AssertionError("must not send")

    for method in ("printer.gcode.script", "printer.print.pause", "printer.emergency_stop", "machine.reboot"):
        with pytest.raises(MethodNotAllowed):
            await driver._call(NoSocket(), method, {"script": "M104 S0"})


async def test_controls_are_disabled():
    driver = make_driver(1)
    for call in (driver.pause(), driver.resume(), driver.cancel(), driver.set_speed(100),
                 driver.set_fan("Model fan", 50), driver.set_temp("extruder", 200), driver.set_light(True)):
        with pytest.raises(ControlsDisabled):
            await call


async def test_offline_then_reconnects_on_schedule(fake_server):
    reconnect = 0.4
    driver = make_driver(fake_server.port, reconnect=reconnect)
    driver.start()
    try:
        await wait_for(lambda: driver.status.state == "printing")
        seen_at = driver.status.last_seen
        attempts_before = driver.connection_attempts

        offline_for = 1.5
        await asyncio.to_thread(fake_server.offline, offline_for)
        await wait_for(lambda: driver.status.state == "offline")
        assert driver.status.last_seen >= seen_at  # last-seen time is kept while offline
        assert driver.status.started_at is None

        await wait_for(lambda: driver.status.state == "printing", timeout=offline_for + 3 * reconnect + 2)
        attempts = driver.connection_attempts - attempts_before
        # One attempt per reconnect interval at most, never a burst.
        assert attempts <= int(offline_for / reconnect) + 2
        assert fake_server.fake.max_concurrent == 1
    finally:
        await driver.stop()


async def test_unreachable_printer_is_offline_and_waits():
    from .conftest import free_port

    driver = make_driver(free_port(), reconnect=0.5)  # nothing listens here
    driver.start()
    try:
        await wait_for(lambda: driver.status.state == "offline")
        await asyncio.sleep(1.2)
        assert driver.connection_attempts <= 4  # ~1 + 1.2/0.5, not a tight loop
        assert driver.status.last_seen is None
    finally:
        await driver.stop()


def test_default_reconnect_is_30_seconds():
    cfg = PrinterConfig(id="k1c", name="K1C", host="127.0.0.1")
    assert MoonrakerDriver(cfg).reconnect_seconds == 30.0


async def test_file_notices_are_only_logged(fake_server, caplog):
    driver = make_driver(fake_server.port)
    driver.start()
    try:
        await wait_for(lambda: driver.status.state == "printing")
        sent_before = list(fake_server.fake.methods)
        caplog.set_level("INFO", logger="app.drivers.moonraker")
        params = [{"action": "create_file", "item": {"root": "gcodes", "path": "rail_B.gcode", "size": 1234}}]
        await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(
            fake_server.fake.notify("notify_filelist_changed", params), fake_server.loop))
        await wait_for(lambda: "notify_filelist_changed" in caplog.text)
        assert "rail_B.gcode" in caplog.text
        assert driver.status.state == "printing"
        assert fake_server.fake.methods == sent_before  # nothing asked of the printer
        assert fake_server.fake.http_requests == []
    finally:
        await driver.stop()
