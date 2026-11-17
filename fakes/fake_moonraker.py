"""A fake Moonraker for tests and local development.

Speaks the parts of Moonraker's websocket JSON-RPC that printer-api uses
(printer.objects.subscribe + notify_status_update) and answers the read-only
GETs that `doctor --probe` makes. It replays printing, paused, idle and
complete states, and can pretend to go offline.

    python fakes/fake_moonraker.py --port 7125 --scenario printing

Dev controls (not part of real Moonraker):
    POST /fake/scenario/{printing|paused|idle|complete|cancelled}
    POST /fake/offline?seconds=60     drop connections and refuse new ones
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import copy
import json
import time
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

# What a real K1C (stock firmware, Creality "nexusp" Moonraker) listed on 2026-11-17.
K1C_OBJECTS = [
    "gcode", "webhooks", "configfile", "mcu", "mcu nozzle_mcu", "mcu leveling_mcu", "box", "cutter_module",
    "gcode_move", "print_stats", "virtual_sdcard", "stepper_enable", "heaters",
    "temperature_sensor chassis", "temperature_sensor main_board", "heater_bed", "pause_resume",
    "filament_switch_sensor filament_sensor", "heater_fan hotend_fan", "fan_generic model_fan",
    "fan_generic side_fan", "fan_generic chassis_fan", "output_pin LED", "probe", "bed_mesh",
    "display_status", "exclude_object", "idle_timeout", "system_stats", "toolhead", "extruder",
]

TOTAL_SECONDS = 2 * 3600 + 1 * 60  # a 2h01m job
TOTAL_LAYERS = 240


def base_objects() -> dict[str, dict[str, Any]]:
    return {
        "print_stats": {"state": "standby", "filename": "", "print_duration": 0.0, "total_duration": 0.0,
                        "message": "", "info": {"current_layer": None, "total_layer": None}},
        "virtual_sdcard": {"progress": 0.0, "is_active": False, "file_position": 0},
        "display_status": {"progress": 0.0, "message": None},
        "extruder": {"temperature": 24.0, "target": 0.0},
        "heater_bed": {"temperature": 23.0, "target": 0.0},
        "gcode_move": {"speed_factor": 1.0},
        "fan_generic model_fan": {"speed": 0.0, "rpm": None},
        "fan_generic side_fan": {"speed": 0.0, "rpm": None},
        "fan_generic chassis_fan": {"speed": 0.0, "rpm": None},
        "output_pin LED": {"value": 1.0},
    }


class FakeMoonraker:
    def __init__(self, scenario: str = "printing", tick_seconds: float = 1.0,
                 filename: str = "phone_stand_v3.gcode", start_progress: float = 0.62):
        self.tick_seconds = tick_seconds
        self.filename = filename
        self.start_progress = start_progress
        self.objects = base_objects()
        self.offline_until = 0.0
        self.current_connections = 0
        self.max_concurrent = 0
        self.total_connections = 0
        self.methods: list[str] = []
        self.http_requests: list[str] = []
        self._sockets: set[WebSocket] = set()
        self._subs: dict[WebSocket, dict[str, Any]] = {}
        self.set_scenario(scenario)

    # --- scenarios -------------------------------------------------------------------
    def set_scenario(self, name: str) -> dict[str, dict[str, Any]]:
        before = copy.deepcopy(self.objects)
        o = self.objects
        ps, sd = o["print_stats"], o["virtual_sdcard"]
        if name in ("printing", "paused"):
            if ps["state"] not in ("printing", "paused"):
                self._set_progress(self.start_progress)
            ps.update(state=name, filename=self.filename, message="")
            sd["is_active"] = name == "printing"
            o["extruder"].update(temperature=219.8, target=220.0)
            o["heater_bed"].update(temperature=60.1, target=60.0)
            o["fan_generic model_fan"]["speed"] = 1.0 if name == "printing" else 0.0
            o["fan_generic side_fan"]["speed"] = 0.4
            o["fan_generic chassis_fan"]["speed"] = 0.3
        elif name in ("complete", "cancelled"):
            if name == "complete":
                self._set_progress(1.0)
            ps.update(state=name, filename=ps["filename"] or self.filename)
            sd["is_active"] = False
            o["extruder"]["target"] = 0.0
            o["heater_bed"]["target"] = 0.0
            o["fan_generic model_fan"]["speed"] = 0.0
        elif name == "idle":
            o.clear()
            o.update(base_objects())
        else:
            raise ValueError(f"unknown scenario {name}")
        self.scenario = name
        return diff(before, self.objects)

    def _set_progress(self, p: float) -> None:
        o = self.objects
        o["virtual_sdcard"]["progress"] = round(p, 4)
        o["display_status"]["progress"] = round(p, 4)
        o["print_stats"]["print_duration"] = round(TOTAL_SECONDS * p, 1)
        o["print_stats"]["total_duration"] = round(TOTAL_SECONDS * p + 95, 1)
        o["print_stats"]["info"] = {"current_layer": max(1, int(TOTAL_LAYERS * p)), "total_layer": TOTAL_LAYERS}

    def tick(self) -> dict[str, dict[str, Any]]:
        """Advance the simulated job by one step and return what changed."""
        before = copy.deepcopy(self.objects)
        o = self.objects
        if self.scenario == "printing":
            p = o["virtual_sdcard"]["progress"] + 0.0005
            if p >= 1.0:
                return self.set_scenario("complete")
            self._set_progress(p)
            o["extruder"]["temperature"] = 220.0 + (0.3 if int(time.time()) % 2 else -0.2)
        elif self.scenario == "paused":
            o["print_stats"]["total_duration"] = round(o["print_stats"]["total_duration"] + self.tick_seconds, 1)
        return diff(before, self.objects)

    # --- offline ---------------------------------------------------------------------
    @property
    def offline(self) -> bool:
        return time.monotonic() < self.offline_until

    async def go_offline(self, seconds: float) -> None:
        self.offline_until = time.monotonic() + seconds
        for ws in list(self._sockets):
            try:
                await ws.close(code=1001)
            except Exception:
                pass

    # --- protocol --------------------------------------------------------------------
    def _status_for(self, wanted: dict[str, Any]) -> dict[str, Any]:
        out = {}
        for name, fields in wanted.items():
            if name not in self.objects:
                continue  # Klipper skips objects it doesn't have
            obj = self.objects[name]
            out[name] = copy.deepcopy(obj if not fields else {k: obj[k] for k in fields if k in obj})
        return out

    async def handle_ws(self, ws: WebSocket) -> None:
        if self.offline:
            await ws.close(code=1013)
            return
        await ws.accept()
        self._sockets.add(ws)
        self.current_connections += 1
        self.total_connections += 1
        self.max_concurrent = max(self.max_concurrent, self.current_connections)
        try:
            while True:
                msg = json.loads(await ws.receive_text())
                method = msg.get("method")
                self.methods.append(method)
                if method == "printer.objects.subscribe":
                    wanted = msg.get("params", {}).get("objects", {})
                    self._subs[ws] = wanted
                    await ws.send_text(json.dumps({"jsonrpc": "2.0", "id": msg.get("id"), "result": {
                        "eventtime": time.monotonic(), "status": self._status_for(wanted)}}))
                else:
                    await ws.send_text(json.dumps({"jsonrpc": "2.0", "id": msg.get("id"), "error": {
                        "code": -32601, "message": f"Method not found: {method}"}}))
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            self._sockets.discard(ws)
            self._subs.pop(ws, None)
            self.current_connections -= 1

    async def push(self, changes: dict[str, dict[str, Any]]) -> None:
        if not changes:
            return
        for ws, wanted in list(self._subs.items()):
            payload = {k: v for k, v in changes.items() if k in wanted}
            if not payload:
                continue
            try:
                await ws.send_text(json.dumps({"jsonrpc": "2.0", "method": "notify_status_update",
                                               "params": [payload, time.monotonic()]}))
            except Exception:
                pass

    async def ticker(self) -> None:
        while True:
            await asyncio.sleep(self.tick_seconds)
            await self.push(self.tick())

    # --- app -------------------------------------------------------------------------
    def app(self) -> Starlette:
        fake = self

        def result(data: Any, path: str) -> JSONResponse:
            fake.http_requests.append(path)
            if fake.offline:
                return JSONResponse({"error": "offline"}, status_code=503)
            return JSONResponse({"result": data})

        async def server_info(r: Request):
            return result({"klippy_connected": True, "klippy_state": "ready", "nexusp_version": "fake-0.1", "api_version_string": "1.4.0",
                           "components": ["database", "file_manager", "klippy_apis", "history"]}, r.url.path)

        async def printer_info(r: Request):
            return result({"state": "ready", "software_version": "fake-klipper", "hostname": "fake-k1c"}, r.url.path)

        async def objects_list(r: Request):
            return result({"objects": K1C_OBJECTS}, r.url.path)

        async def scenario(request: Request):
            await fake.push(fake.set_scenario(request.path_params["name"]))
            return JSONResponse({"scenario": fake.scenario})

        async def offline(request: Request):
            await fake.go_offline(float(request.query_params.get("seconds", "60")))
            return JSONResponse({"offline_for": request.query_params.get("seconds", "60")})

        @contextlib.asynccontextmanager
        async def lifespan(app):
            task = asyncio.create_task(fake.ticker())
            yield
            task.cancel()

        return Starlette(routes=[
            Route("/server/info", server_info),
            Route("/printer/info", printer_info),
            Route("/printer/objects/list", objects_list),
            Route("/fake/scenario/{name}", scenario, methods=["POST"]),
            Route("/fake/offline", offline, methods=["POST"]),
            WebSocketRoute("/websocket", fake.handle_ws),
        ], lifespan=lifespan)


def diff(before: dict, after: dict) -> dict:
    out: dict[str, dict[str, Any]] = {}
    for name, obj in after.items():
        old = before.get(name, {})
        changed = {k: v for k, v in obj.items() if old.get(k) != v}
        if changed:
            out[name] = changed
    return out


if __name__ == "__main__":
    import uvicorn

    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=7125)
    ap.add_argument("--scenario", default="printing")
    ap.add_argument("--tick", type=float, default=1.0)
    a = ap.parse_args()
    uvicorn.run(FakeMoonraker(a.scenario, a.tick).app(), host=a.host, port=a.port, log_level="warning")
