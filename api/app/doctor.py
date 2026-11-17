"""Setup check.

    python -m app.doctor            validate .env and printers.yaml, touch nothing
    python -m app.doctor --probe    also make a few gentle read-only GETs per printer

--probe sends at most three GET requests per printer, one at a time with a pause
between them, no retries, and only to the configured Moonraker port. It never
sends G-code or changes anything. Don't run it while a print is running unless
you're happy for the printer to answer three status requests.
"""

from __future__ import annotations

import argparse
import json
import sys
import time

import httpx

from .config import Settings, load_printers

PROBE_PATHS = ("/server/info", "/printer/info", "/printer/objects/list")
INTERESTING = ("fan", "pin", "led", "light", "box", "cfs", "filament", "heater", "temperature")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.doctor", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--probe", action="store_true", help="make read-only GETs to each printer")
    parser.add_argument("--printer", help="only this printer id")
    parser.add_argument("--json", action="store_true", help="print the full responses")
    args = parser.parse_args(argv)

    settings = Settings()
    print(f"BIND_ADDRESS {settings.bind_address}  PORT {settings.port}")
    print(f"DATA_DIR {settings.data_dir}  LIBRARY_ROOT {settings.library_root}  TZ {settings.tz}")
    if settings.bind_address in ("0.0.0.0", "::"):
        print("WARNING: BIND_ADDRESS listens on every interface; use a private (VPN or LAN) address")
    try:
        printers = load_printers(settings.printers_file)
    except Exception as exc:
        print(f"ERROR in {settings.printers_file}: {exc}")
        return 1
    if not printers:
        print(f"No printers in {settings.printers_file}")
        return 1
    for p in printers:
        print(f"- {p.id}: {p.name} ({p.type}) at {p.host}:{p.port}, camera {p.camera}, cfs {p.cfs}")

    if not args.probe:
        return 0

    for p in printers:
        if args.printer and p.id != args.printer:
            continue
        if p.type != "moonraker":
            print(f"\n{p.id}: probing {p.type} printers isn't supported yet")
            continue
        print(f"\n{p.id}: probing http://{p.host}:{p.port} (read-only)")
        with httpx.Client(timeout=5.0) as client:
            for i, path in enumerate(PROBE_PATHS):
                if i:
                    time.sleep(2)
                try:
                    r = client.get(f"http://{p.host}:{p.port}{path}")
                except httpx.HTTPError as exc:
                    print(f"  GET {path}: failed ({type(exc).__name__}); stopping here")
                    break
                print(f"  GET {path}: {r.status_code}")
                if r.status_code != 200:
                    continue
                result = r.json().get("result", {})
                if args.json:
                    print(json.dumps(result, indent=2))
                if path == "/printer/objects/list":
                    objects = result.get("objects", [])
                    picks = [o for o in objects if any(k in o.lower() for k in INTERESTING)]
                    print(f"  {len(objects)} objects; fans, pins, lights, heaters and CFS-like ones:")
                    for o in picks:
                        print(f"    {o}")
                elif path == "/server/info":
                    print(f"  moonraker {result.get('moonraker_version')}, klippy {result.get('klippy_state')}")
                    print(f"  components: {', '.join(result.get('components', []))}")
                elif path == "/printer/info":
                    print(f"  klipper {result.get('software_version')}, state {result.get('state')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
