# Camera relay

The app shows each printer's camera through a relay on the server. The server
holds **one** connection to the printer's camera while anyone is watching, and
passes the video on, untouched, to every open page.

```
printer camera ──WebRTC──> go2rtc ──fMP4 over WebSocket──> printer-api ──same WebSocket──> browsers
 (port 8000)              (sidecar,                         /api/printers/{id}/camera/ws
                           not published)
```

## Why a relay

- The printer only ever sends one stream, however many tabs or phones are open.
  That's what makes it safe to start the camera on its own on the dashboard and
  the printer page.
- Browsers only talk to the app's own port. They no longer need a route to the
  printer's address, so the camera works wherever the app works.
- The video is H.264 in fragmented MP4, played with Media Source Extensions.
  Safari (macOS and iPhone, 17.1+), Chrome, Edge and Firefox play it without
  extra codecs.
- Later, the server can grab still frames for thumbnails and timelapses.

## How it works

1. A page opens `/api/printers/{id}/camera/ws`. printer-api checks the printer
   exists, has a `creality_webrtc` camera and is online, then proxies the
   WebSocket to go2rtc's `/api/ws`. The upstream URL names the stream after
   the printer, and its source points back at printer-api.
2. If go2rtc isn't already streaming that printer, its Creality source posts a
   WebRTC offer to the source URL. That URL is a private callback on
   printer-api, `/api/internal/camera/<token>/<id>`. The token is random on
   every start and is only ever given to go2rtc.
3. printer-api fixes the offer for the K1C and sends it through the same
   handshake the direct mode uses: one POST to
   `http://<host>:8000/call/webrtc_local`, at most one per printer every
   `CAMERA_MIN_INTERVAL` seconds, with a 10 s timeout and no retries. It then
   fixes the answer and hands it back to go2rtc.
4. The video flows from the printer to go2rtc. go2rtc copies the H.264 into
   fragmented MP4 without re-encoding it, and sends that to every viewer.
5. When a page closes, printer-api keeps its upstream open for 20 seconds, so
   moving between the dashboard and a printer page doesn't make a new
   handshake. After that, go2rtc drops the connection to the printer once it
   has no viewers left.

### K1C quirks the app fixes

These fixes are in `api/app/relay.py`, with tests in `api/tests/test_relay.py`.

- **The offer must be `sendrecv`.** The K1C only sends video for a
  `sendrecv` offer, which is what its own page at `:8000` sends. go2rtc offers
  `recvonly`, so printer-api rewrites the direction. No media is ever sent to
  the printer.
- **The answer's direction.** The answer is flipped from `sendrecv` to
  `sendonly`, so go2rtc's receive-only connection accepts it.
- **A payload type missing from the answer.** The K1C sends H.264 on a
  payload type that was in the offer but left out of its answer
  ([go2rtc #2024](https://github.com/AlexxIT/go2rtc/issues/2024)). Any offered
  H.264 payload type the answer leaves out is added back, **straight after the
  answer's first format**, the ones with the K1C's announced profile first.
  go2rtc drops that first format and binds viewers to the next one, so this
  puts viewers on the type the video arrives on. (Added at the end, as before,
  viewers sat on an empty receiver: browsers got only the init segment and
  still-frame requests hung.)
- go2rtc's own Creality fix drops the answer's first format, which on the K1C
  is a placeholder.

### Fixes to the video on its way to the page

go2rtc's fragmented MP4 from the K1C has faults that iPhone Safari won't
accept (desktop Chrome plays it anyway). printer-api fixes them as it passes
the stream on (`api/app/fmp4.py`, tests in `api/tests/test_fmp4.py`):

- **The init segment is a placeholder.** go2rtc writes avcC before it has
  seen any video: Baseline 1.0, 128x96. The real SPS/PPS (Main 3.0, 800x600)
  come in-band on keyframes. The init segment is held until the first
  fragment with an SPS and PPS, then rebuilt from them (avcC, and the width
  and height in avc1 and tkhd). If none comes within 60 fragments, it goes
  out as it was.

- **The clock runs about 10x fast.** Frames arrive about 14 times a second,
  but their timestamps say 0.7 s each, so the buffer races ahead of the
  playhead. Each fragment's start time and duration are rewritten from when
  fragments actually arrive (the mean gap over the last 30), laid end to end
  from zero.
- **The codec label is wrong.** go2rtc tells the page `avc1.640029` or
  `avc1.42000A`; the K1C sends Main 3.0. The label is taken from the rebuilt
  avcC instead.

Fragments are only rewritten in place. Anything that doesn't
look as expected is passed on untouched.

### When live video still won't play

The page retries a failed stream twice by itself. After that it shows a still
every 2 seconds from `GET /api/printers/{id}/camera/still.jpg`, with a button
to try live video again. The server asks go2rtc for the still
(`/api/frame.jpeg`), holding one viewer connection of its own while stills are
wanted (let go 20 s after the last one), and shares each still between pages
for 1.5 s. The error text now names the browser's error (for example
`QuotaExceededError` or a media error code).

The last handshake for each printer (the offer, the fixed offer, the answer
and the fixed answer) is saved to `data/camera-handshake-<id>.txt`. Read it
first when the camera shows nothing.

## Setup

`docker compose up -d --build` starts go2rtc (`alexxit/go2rtc:1.9.14`)
alongside printer-api, using `go2rtc.yaml` from the repo. go2rtc publishes no
ports; only printer-api can reach it. Its connection to the printer is
outbound UDP from the container. Docker NAT carries the replies, so the
host's firewall needs no new rules for it.

Settings (`.env`):

| Setting | Default | Meaning |
|---|---|---|
| `GO2RTC_URL` | `http://go2rtc:1984` (from compose) | Where printer-api finds go2rtc. Set it empty (`GO2RTC_URL=`) to turn the relay off. |
| `CAMERA_MIN_INTERVAL` | `5` | At most one camera handshake per printer this often, in seconds. |

`/api/config` reports `"camera_mode": "relay"` or `"direct"`, and the web app
follows it.

## Turning it off (direct mode)

With `GO2RTC_URL=` the app falls back to the earlier design. **Watch live**
sends the browser's own WebRTC offer to the printer through printer-api, and
the video goes straight from the printer to the browser. In this mode the
browser needs a route to the printer's address (see the README), and the
camera only starts when you tap.

## Behaviour and limits

- **Auto-start.** The camera starts by itself on the printer page and on the
  dashboard cards when the printer is online. It stops when the tab is hidden
  and starts again when the tab comes back. There is no Stop button; leave the
  page or hide the tab to stop it.
- **No automatic retries from the page.** If the camera fails, the page says
  why and shows **Try again**. go2rtc may retry a dropped stream while someone
  is still watching, but every attempt goes through printer-api's rate limit,
  so the printer sees at most one handshake per printer every
  `CAMERA_MIN_INTERVAL` seconds.
- **Live** only shows once video is actually playing. If nothing plays within
  20 seconds, the page says "No video arrived from the camera."
- **The fake printer** (`--profile fake`) answers the handshake with a
  placeholder, so with the relay on, its camera ends in an error. That's
  expected.

## Testing

`api/tests/test_relay.py` covers the SDP fixes, the token-protected callback,
the WebSocket proxy (against a stand-in for go2rtc) and the 20-second linger.

The full chain was also run end to end with a real go2rtc 1.9.14. A second
go2rtc streamed a test pattern behind a stand-in for the K1C's
`/call/webrtc_local`, and headless Chromium played the video on both the
printer page and the dashboard. That run can't reproduce the real K1C's
behaviour, so the first real test is on the printer itself, while it's idle.
