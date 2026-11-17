# Printer app

A private web app for watching and controlling 3D printers: a dashboard of
every printer and a page per printer, on desktop and phone, with the printer's
live camera and its controls. The file library and printing from the app come
in later versions (see the roadmap).

It runs as one Docker Compose stack on any machine that can reach your
printers, and is meant to be reached over a private network (Tailscale,
WireGuard or your LAN).

```
.
  compose.yaml            printer-api (+ a fake printer under the "fake" profile)
  .env.example            settings
  printers.example.yaml   your printers
  api/                    FastAPI + SQLite server, drivers/, tests/
  web/                    React + Vite + Tailwind + shadcn/ui app (built into the image)
  fakes/                  fake Moonraker for tests and local development
```

## How it talks to printers

printer-api keeps **one websocket per printer** (Moonraker's
`ws://<host>:7125/websocket`), subscribes to the status objects once, and the
printer pushes changes from then on, so there's no polling. Browsers get those
changes over one WebSocket from printer-api.

These rules are enforced in the driver, not just the UI:

- one connection per printer, only to the host and port in `printers.yaml`; no scanning;
- when a printer drops or can't be reached, it shows as offline with its
  last-seen time, and the app tries **one** reconnect every 30 seconds;
- read-only unless `CONTROLS_ENABLED=true`: until then the only method the
  driver sends is `printer.objects.subscribe`, and the control endpoints return
  `501`. With controls on, only the fixed commands under [Controls](#controls)
  can be sent.

## Live camera

Creality printers on stock firmware stream their camera over WebRTC. By
default the app runs a small relay, [go2rtc](https://github.com/AlexxIT/go2rtc),
next to printer-api. The server holds one connection to the printer's camera
while anyone is watching, and passes the video on to every open page. Both
pages show the 3D model of the print first; the **Camera** button switches
to the live camera, which connects only while it is shown and stops when you
switch back or hide the tab. Browsers only talk to the app, so they need no route to the
printer. How it works, the K1C quirks it fixes and its limits are in
[docs/camera-relay.md](docs/camera-relay.md).

printer-api allows at most one camera handshake per printer every 5 seconds
(`CAMERA_MIN_INTERVAL`).

### Direct mode (relay off)

Set `GO2RTC_URL=` (empty) in `.env` to turn the relay off. On the printer page,
**Watch live** then sends the browser's WebRTC offer to printer-api, which
passes it to the printer's `http://<host>:<camera_port>/call/webrtc_local`
(port 8000 by default) and returns the answer. The video flows from the printer
straight to your browser, so your device must be able to reach the printer's
address:

- **Tailscale:** on the machine running the app, advertise a route for just the
  printer's address and approve it in the admin console:
  ```
  echo 'net.ipv4.ip_forward = 1' | sudo tee /etc/sysctl.d/99-tailscale.conf
  sudo sysctl -p /etc/sysctl.d/99-tailscale.conf
  sudo tailscale set --advertise-routes=<printer-ip>/32
  ```
  Phones, Macs and Windows accept routes by default; Linux clients need
  `sudo tailscale set --accept-routes`.
- **WireGuard:** add the printer's IP to the client's `AllowedIPs`.
- **LAN:** nothing to do.

## Controls

With `CONTROLS_ENABLED=true` in `.env`, the printer page's buttons work:
Pause, Resume and Stop (Pause and Stop ask first), a print speed slider (50 to
160%), fan sliders, nozzle and bed targets, and the chamber light. Each button
sends exactly one fixed command. On Moonraker printers:

| Control | Command | Allowed |
| --- | --- | --- |
| Pause / Resume / Stop | `printer.print.pause` / `resume` / `cancel`, or the `PAUSE` / `RESUME` / `CANCEL_PRINT` macros if the printer's Moonraker lacks those methods | while printing or paused |
| Speed | `M220 S20` to `S160`, steps of 10 | while printing or paused |
| Fans | `SET_FAN_SPEED FAN=<name> SPEED=0..1`, steps of 10% | while online |
| Nozzle / bed | `M104 S<°C>` / `M140 S<°C>`: 0, or 30 to 300 (nozzle) / 100 (bed) | while online |
| Light | `SET_PIN PIN=<pin> VALUE=0/1` | while online |

The rules are enforced on the server, not just in the page. The driver only
sends G-code that matches its allowlist exactly, and the API never accepts
G-code text. It allows one command per printer at a time, at most one a
second, and never retries one. Nothing is sent while the printer is offline.
Every press, including refused ones, goes into the action log. The printer
page shows the last 20 presses, and `GET /api/printers/{id}/actions` returns
the full history.

## 3D preview of the print

The printer page and each dashboard card draw the toolpath of the file being
printed in 3D, with the printed part in the accent colour and a dot where the
nozzle is. Drag to turn it, scroll to zoom.

To do that, printer-api downloads the G-code being printed from the printer's
Moonraker (`GET /server/files/gcodes/<file>`) **once per print**, when it first
sees the print. It never retries, even if the download fails, keeps the file in
`DATA_DIR/gcode/<printer>/` (so a restart doesn't fetch it again), and caps it
at 200 MB. Browsers get it from printer-api, never from the printer. Set
`GCODE_PREVIEW=false` to turn this off.

## Setup

1. `cp .env.example .env` and set `BIND_ADDRESS` to a private address the app
   should listen on (a Tailscale IP, a WireGuard IP or a LAN IP). The default,
   `127.0.0.1`, only listens on the machine itself.
2. `cp printers.example.yaml printers.yaml` and fill in your printers.
3. Optional: check the config without touching any printer:
   `docker compose run --rm printer-api python -m app.doctor`
4. `docker compose up -d --build`
5. Open `http://<BIND_ADDRESS>:<PORT>` (8090 by default).

The container runs as `PUID:PGID` (1000:1000 by default) and writes its
database to `DATA_DIR` (`./data`).

If `BIND_ADDRESS` is a Tailscale IP, Docker can only publish on it once
Tailscale is up. If the stack starts before Tailscale after a reboot, run
`docker compose up -d` again.

### Finding your printer's object names

Fan and light names differ between printers. The defaults in
`printers.example.yaml` are a stock-firmware K1C's. To see your printer's,
run this **once, while the printer is idle**:

```
docker compose run --rm printer-api python -m app.doctor --probe
```

It makes three read-only GET requests per printer (`/server/info`,
`/printer/info`, `/printer/objects/list`), one at a time with a pause between
them and no retries, and prints the fans, pins, lights and heaters it finds.

## Try it without a printer

```
PRINTERS_FILE=./printers.fake.yaml docker compose --profile fake up -d --build
```

The fake Moonraker plays a print, and a fake camera answers the handshake (no real video). Switch states or drop it offline:

```
docker compose exec fake-moonraker python -c "import urllib.request as u; u.urlopen(u.Request('http://127.0.0.1:7125/fake/scenario/paused', method='POST'))"
# scenarios: printing, paused, idle, complete, cancelled
# offline:   .../fake/offline?seconds=60
```

## Development

```
# API
cd api
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest                                      # runs against the fake Moonraker only

python ../fakes/fake_moonraker.py --port 7125 &   # a fake printer
PRINTERS_FILE=../printers.dev.yaml python -m app  # http://127.0.0.1:8090

# Web app (proxies /api to 127.0.0.1:8090)
cd web
npm install
npm run dev
```

The database schema is managed with Alembic and migrated at startup. After
changing a table in `api/app/db.py`, add a migration with
`alembic revision --autogenerate -m "..."` from `api/`.

## Settings (.env)

| Setting | What it does | Default |
| --- | --- | --- |
| `BIND_ADDRESS` | Private address the web app listens on | `127.0.0.1` |
| `PORT` | Web port | `8090` |
| `DATA_DIR` | Database | `./data` |
| `LIBRARY_ROOT` | Print files (used from the library version on) | `./library` |
| `PRINTERS_FILE` | Which printers file to mount | `./printers.yaml` |
| `TZ` | Time zone for times in the app | `UTC` |
| `CONTROLS_ENABLED` | Turns on the printer controls | `false` |
| `GCODE_PREVIEW` | Fetch the current print's G-code once per print for the 3D preview | `true` |
| `CAMERA_MIN_INTERVAL` | Seconds between camera handshakes per printer | `5` |
| `GO2RTC_URL` | Camera relay address; empty turns the relay off (direct mode) | `http://go2rtc:1984` |
| `PUID` / `PGID` | User the container runs as | `1000` |
| `IMPORT_TOKEN` | Enables the import endpoint (later version) | empty = off |
| `TAILSCALE_SOCKET` | Device names in the action log (later version) | unset |
