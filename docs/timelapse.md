# Time-lapses

While a print runs, the app saves one camera frame per layer. Open a print in
**History** to see its 3D model; the **Time-lapse** button in the corner swaps
the model for the time-lapse (play, step, scrub, 10 / 24 / 60 fps), the same
way **Camera** works on the printer page. Desktop only.

## How it works (`api/app/timelapse.py`)

- It needs the camera relay (`docs/camera-relay.md`). While a printer with a
  `creality_webrtc` camera is printing, the app holds **one** viewer connection
  to go2rtc for the whole print, like an open camera page. It lets the camera
  go while the print is paused or the printer is offline.
- On every layer change it asks go2rtc for a still (`/api/frame.jpeg`) and
  saves it. Layers come from the printer, or from the G-code when the printer
  doesn't report them (`GCODE_PREVIEW`). With no layer information it saves a
  frame every 30 s instead. When the print finishes, one more frame of the
  finished part is saved.
- A frame that doesn't arrive within 10 s is skipped and shows as a gap. After
  two misses in a row the app drops its viewer and deletes the stream in go2rtc,
  so a stuck request can't keep the camera open, then starts again.
- The app never talks to the printer for this; go2rtc's connection goes through
  the app's rate-limited camera handshake.

## Storage

Frames go to `TIMELAPSE_DIR/<printer>/<print id>/000001.jpg …`, with one line
per frame in `frames.jsonl` (frame number, layer, time). The K1C's camera gives
800×600 JPEGs of about 15 KB, so a print takes roughly 15 KB × its layer count:
about 2.4 MB for 159 layers, 18 MB for 1,200. Frames are kept until you delete
them.

Nothing is written unless the folder holds a `.timelapse-root` file and has at
least `TIMELAPSE_MIN_FREE_GB` free. Docker creates a missing bind-mount folder
empty, so without the marker an unmounted NAS could fill the system disk.

| Setting | Default | Meaning |
|---|---|---|
| `TIMELAPSE_DIR` | `./timelapse` | Folder on the host for frames (mounted at `/timelapse`). |
| `TIMELAPSE` | `true` | `false` turns recording off. |
| `TIMELAPSE_MIN_FREE_GB` | `20` | Stop saving below this much free space. |

Setup on a NAS mount, once:

```
sudo install -d -o "$USER" -g "$USER" /mnt/media/printer-timelapse
touch /mnt/media/printer-timelapse/.timelapse-root
echo TIMELAPSE_DIR=/mnt/media/printer-timelapse >> .env
docker compose up -d --build
```

## In History

- Prints from before this feature, or with no camera, have no Time-lapse button.
- If frames were saved but the folder can't be read (the NAS is away), the
  time-lapse view says so and offers Try again; the rest of the page works.
- Gaps (printer offline, app restart, a frame that didn't come) are marked on
  the scrubber, and the player names the missing layers.
- A print that's still running opens the printer page.
