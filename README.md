# Printer app

A private web app for watching 3D printers: a dashboard of every printer and a
page per printer, on desktop and phone, with the printer's live camera. It
shows **status only** so far: controls, the file library and printing from
the app come in later versions (see the roadmap).

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
- read-only: the only method the driver will send is `printer.objects.subscribe`.
  The control endpoints exist but return `501 Not Implemented` and never reach a printer.

## Live camera

Creality printers on stock firmware stream their camera over WebRTC. On the
printer page, **Watch live** sends the browser's WebRTC offer to printer-api,
which passes it to the printer's `http://<host>:<camera_port>/call/webrtc_local`
(port 8000 by default) and returns the answer. That is the only thing
printer-api does for the camera: the video itself flows from the printer
straight to your browser, so your device must be able to reach the printer's
address.

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

The camera only starts when you press Watch live, stops when you leave the
page or hide the tab, and printer-api allows at most one handshake per printer
every 5 seconds (`CAMERA_MIN_INTERVAL`).

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
| `CAMERA_MIN_INTERVAL` | Seconds between camera handshakes per printer | `5` |
| `PUID` / `PGID` | User the container runs as | `1000` |
| `IMPORT_TOKEN` | Enables the import endpoint (later version) | empty = off |
| `TAILSCALE_SOCKET` | Device names in the action log (later version) | unset |
