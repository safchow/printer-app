# Printer app

A private web app for watching and controlling 3D printers: a dashboard of
every printer and a page per printer, on desktop and phone, with the printer's
live camera and its controls, with a 3D view of each print. It also keeps a
copy of every file you send to the printer (from Creality Print as usual), lets
you sort them with labels and records each print. The app doesn't upload or
start prints; there is no upload flow.

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

## Files and history (file capture)

When you send a file to the printer, Moonraker tells printer-api over the
websocket it already has open (`notify_filelist_changed`). The K1C announces the
file (`create_file`) while it is still arriving, can then go quiet for 10+
seconds mid-transfer, and sends `modify_file` with the full size when it's done.
printer-api waits for that, then downloads the file from the printer **once**
(`GET /server/files/gcodes/<file>`) and keeps it only if its size matches the
notice; a partial copy is thrown away. If no `modify_file` ever comes, it tries
once two minutes after the last notice and again keeps the copy only if the size
matches what the printer announced.
Hidden folders such as `.thumbs/` are never fetched, and only `.gcode` and
`.3mf` files are kept.

- Files are stored by content (`LIBRARY_ROOT/files/<sha256>`), so sending the
  same file again, even under a new name, is one entry that keeps its label.
- New files land in the **Inbox** on the Files page. Give a file a label to
  file it away; labels are made as you type them.
- Time, material, colours, filament and layers come from the slicer's comment
  header, plus its largest thumbnail.
- Every copy is kept until you delete it in the app. Deleting a file on the
  printer doesn't touch the app's copy, and deleting in the app doesn't touch
  the printer.
- The **History** page lists every print the app has seen start, with how it
  ended, linked to its file. A file already captured is reused for the 3D
  preview, so a print of it costs no download at all.

Set `CAPTURE_FILES=false` to turn this off (the 3D preview then works as
before).

## Themes

The appearance button in the top bar opens a theme picker: **System** (follows
your device's light or dark setting) and 100 base themes, 50 dark and 50 light,
with search and a Dark / Light filter. Each browser remembers its own choice.

The base themes live in `api/app/themes/base.yaml`, generated by
`api/scripts/make_base_themes.py`, which checks every text colour reads at
4.5:1 or better on the surfaces it sits on.

To add your own, create `themes.yaml` in the data folder (`./data/themes.yaml`
with Docker; or point `THEMES_FILE` at it when running without Docker). See
`themes.example.yaml`. The app picks up changes on the next page load, with no
restart.

```yaml
default:            # optional: what System uses
  dark: carbon-spool
themes:
  - id: carbon-spool          # new theme, shown under "Yours" at the top
    name: Carbon spool
    mode: dark
    extends: carbon-teal      # start from any theme, change only what differs
    radius: 6                 # corner size in px, 0-24
    font: inter               # inter, system, mono or serif
    colors:
      accent: "#14b8a6"
  - id: zinc-blue             # a base theme's id changes that theme
    colors:
      accent: "#7c3aed"
```

A theme needs `mode` and the colours `background`, `foreground` and `accent`
(directly or through `extends`); the rest (cards, borders, muted text, warning
colours...) are worked out from those unless you set them. The full list of
colours is at the top of `base.yaml`. A theme with a bad colour or text that
would be hard to read is left out and the reason is logged; it never stops
the app.

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
| `LIBRARY_ROOT` | Captured print files | `./library` |
| `PRINTERS_FILE` | Which printers file to mount | `./printers.yaml` |
| `TZ` | Time zone for times in the app | `UTC` |
| `THEMES_FILE` | Your own themes file (see Themes) | `DATA_DIR/themes.yaml` |
| `CONTROLS_ENABLED` | Turns on the printer controls | `false` |
| `GCODE_PREVIEW` | Fetch the current print's G-code once per print for the 3D preview | `true` |
| `CAPTURE_FILES` | Keep a copy of each file sent to the printer, and the print history | `true` |
| `CAMERA_MIN_INTERVAL` | Seconds between camera handshakes per printer | `5` |
| `GO2RTC_URL` | Camera relay address; empty turns the relay off (direct mode) | `http://go2rtc:1984` |
| `PUID` / `PGID` | User the container runs as | `1000` |
| `IMPORT_TOKEN` | Enables the import endpoint (later version) | empty = off |
| `TAILSCALE_SOCKET` | Device names in the action log (later version) | unset |
