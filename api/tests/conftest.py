from __future__ import annotations

import asyncio
import socket
import sys
import threading
import time
from pathlib import Path

import pytest
import uvicorn

ROOT = Path(__file__).resolve().parents[2]  # printer/
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api"))

from fakes.fake_moonraker import FakeMoonraker  # noqa: E402


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FakeServer:
    """Runs a FakeMoonraker on a real port in a background thread."""

    def __init__(self, scenario: str = "printing", tick: float = 0.05):
        self.port = free_port()
        self.fake = FakeMoonraker(scenario, tick_seconds=tick)
        config = uvicorn.Config(self.fake.app(), host="127.0.0.1", port=self.port,
                                log_level="warning", lifespan="on", ws="websockets-sansio")
        self.server = uvicorn.Server(config)
        self.loop: asyncio.AbstractEventLoop | None = None
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        self.loop = asyncio.new_event_loop()
        self.loop.run_until_complete(self.server.serve())

    def start(self) -> "FakeServer":
        self.thread.start()
        deadline = time.time() + 10
        while not self.server.started:
            if time.time() > deadline:
                raise RuntimeError("fake moonraker did not start")
            time.sleep(0.02)
        return self

    def run(self, coro) -> object:
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(5)

    def scenario(self, name: str) -> None:
        async def go():
            await self.fake.push(self.fake.set_scenario(name))
        self.run(go())

    def offline(self, seconds: float) -> None:
        self.run(self.fake.go_offline(seconds))

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(5)


@pytest.fixture
def fake_server():
    server = FakeServer().start()
    yield server
    server.stop()


@pytest.fixture
def printers_file(tmp_path, fake_server) -> Path:
    path = tmp_path / "printers.yaml"
    path.write_text(f"""
printers:
  - id: k1c
    name: K1C
    type: moonraker
    host: 127.0.0.1
    port: {fake_server.port}
    camera: creality_webrtc
    cfs: true
""")
    return path


async def wait_for(predicate, timeout: float = 5.0, interval: float = 0.02):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(interval)
    raise AssertionError("condition not met in time")


def wait_for_sync(predicate, timeout: float = 5.0, interval: float = 0.02):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    raise AssertionError("condition not met in time")
