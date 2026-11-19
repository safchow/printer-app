"""Fixes for the fragmented MP4 that go2rtc makes from the K1C's camera.

Two things are wrong with it, and iPhone Safari refuses the stream because of
them (desktop Chrome plays it anyway):

* **The clock runs about ten times too fast.** Each fragment holds one frame
  and they arrive about 14 times a second, but their timestamps say 0.7 s per
  frame. The buffer races ahead of the playhead, and Safari's decoder fails.
  The K1C's RTP timestamps are the cause, so this can't be fixed in go2rtc's
  settings. We give every frame a duration measured from when fragments
  actually arrive (averaged over the last couple of seconds), and lay them end to end from zero. The timeline
  has no gaps and no overlaps, and plays at real speed.
* **The codec label is wrong.** go2rtc answers `avc1.640029` (High 4.1) to
  the page, but the stream is Main 3.0. We read the real profile from the
  init segment's avcC box and put that in the answer.

Only box fields are rewritten, in place, so every box keeps its size. If
anything in the stream doesn't look as expected, it is passed on untouched.
"""

from __future__ import annotations

import json
import logging
import re
import struct
import time
from collections import deque
from collections.abc import Callable

log = logging.getLogger(__name__)

DEFAULT_FRAME = 1 / 15  # seconds, until arrivals have been measured
MIN_FRAME = 1 / 60
MAX_FRAME = 1.0
WINDOW = 30  # frame time is the mean gap over this many recent fragments
MAX_GAP = 0.5  # a longer stall counts as this much, so it can't slow the video for long

_CONTAINERS = {b"moov", b"trak", b"mdia", b"minf", b"stbl", b"moof", b"traf"}


def boxes(data: bytes | bytearray, start: int = 0, end: int | None = None):
    """(type, start of box, start of payload, end of box) for each whole box in data[start:end]."""
    end = len(data) if end is None else end
    i = start
    while i + 8 <= end:
        size, kind = struct.unpack_from(">I4s", data, i)
        header = 8
        if size == 1:
            if i + 16 > end:
                return
            size = struct.unpack_from(">Q", data, i + 8)[0]
            header = 16
        elif size == 0:
            size = end - i
        if size < header or i + size > end:
            return  # partial or broken box: stop here
        yield kind, i, i + header, i + size
        i += size


def find(data: bytes | bytearray, path: list[bytes], start: int = 0, end: int | None = None):
    """Every box at `path` (e.g. [b"moov", b"trak"]), as (payload start, box end)."""
    for kind, _s, p, e in boxes(data, start, end):
        if kind != path[0]:
            continue
        if len(path) == 1:
            yield p, e
        elif kind in _CONTAINERS:
            yield from find(data, path[1:], p, e)


def _first(data, path, start=0, end=None):
    return next(find(data, path, start, end), None)


def avc_codec(init: bytes) -> str | None:
    """The real `avc1.PPCCLL` string from the init segment's avcC box, if it has one."""
    stsd = _first(init, [b"moov", b"trak", b"mdia", b"minf", b"stbl", b"stsd"])
    if not stsd:
        return None
    p, e = stsd
    # stsd: version/flags(4), entry count(4), then sample entries.
    for kind, _s, ep, ee in boxes(init, p + 8, e):
        if kind not in (b"avc1", b"avc3"):
            continue
        # VisualSampleEntry: 78 bytes before its child boxes.
        avcc = _first(init, [b"avcC"], ep + 78, ee)
        if avcc and avcc[1] - avcc[0] >= 4:
            prof, compat, level = init[avcc[0] + 1], init[avcc[0] + 2], init[avcc[0] + 3]
            return f"{kind.decode()}.{prof:02X}{compat:02X}{level:02X}"
    return None


def video_track(init: bytes) -> tuple[int, int] | None:
    """(track id, timescale) when the init segment holds exactly one track, and it is video."""
    traks = list(find(init, [b"moov", b"trak"]))
    if len(traks) != 1:
        return None
    p, e = traks[0]
    tkhd = _first(init, [b"tkhd"], p, e)
    hdlr = _first(init, [b"mdia", b"hdlr"], p, e)
    mdhd = _first(init, [b"mdia", b"mdhd"], p, e)
    if not (tkhd and hdlr and mdhd) or init[hdlr[0] + 8:hdlr[0] + 12] != b"vide":
        return None
    v = init[tkhd[0]]
    track_id = struct.unpack_from(">I", init, tkhd[0] + (20 if v == 1 else 12))[0]
    v = init[mdhd[0]]
    timescale = struct.unpack_from(">I", init, mdhd[0] + (20 if v == 1 else 12))[0]
    return (track_id, timescale) if timescale else None


class Retimer:
    """Rewrites each fragment's times so the video plays at the speed it arrives."""

    def __init__(self, timescale: int, clock: Callable[[], float] = time.monotonic):
        self.timescale = timescale
        self.clock = clock
        self.next_time = 0  # in timescale units
        self.frame = DEFAULT_FRAME  # seconds per sample, the mean of `gaps`
        self.gaps: deque[float] = deque(maxlen=WINDOW)
        self.last: tuple[float, int] | None = None  # (arrival, samples) of the previous fragment

    def fragment(self, buf: bytearray, moof_payload: int, moof_end: int) -> bool:
        """Retime one moof in place. False when it can't be (then nothing is changed)."""
        traf = _first(buf, [b"traf"], moof_payload, moof_end)
        if not traf:
            return False
        tfhd = _first(buf, [b"tfhd"], *traf)
        tfdt = _first(buf, [b"tfdt"], *traf)
        trun = _first(buf, [b"trun"], *traf)
        if not (tfhd and tfdt and trun):
            return False

        # trun: which per-sample fields there are, and where they start.
        t = trun[0]
        tr_version, tr_flags = buf[t], int.from_bytes(buf[t + 1:t + 4], "big")
        count = struct.unpack_from(">I", buf, t + 4)[0]
        o = t + 8 + (4 if tr_flags & 0x1 else 0) + (4 if tr_flags & 0x4 else 0)
        fields = [f for f in (0x100, 0x200, 0x400, 0x800) if tr_flags & f]
        if count == 0 or o + 4 * len(fields) * count > trun[1]:
            return False

        # Where the durations live: per sample in trun, or tfhd's default.
        f = tfhd[0]
        th_flags = int.from_bytes(buf[f + 1:f + 4], "big")
        default_at = None
        if th_flags & 0x8:
            default_at = f + 8 + (8 if th_flags & 0x1 else 0) + (4 if th_flags & 0x2 else 0)
        if not tr_flags & 0x100 and default_at is None:
            return False

        now = self.clock()
        if self.last is not None:
            # A mean over a window, not the newest gap: frames held up by the
            # network arrive in a burst right after a pause, and the two even out.
            self.gaps.append(min(MAX_GAP, now - self.last[0]) / self.last[1])
            self.frame = min(MAX_FRAME, max(MIN_FRAME, sum(self.gaps) / len(self.gaps)))
        self.last = (now, count)
        dur = max(1, round(self.frame * self.timescale))

        stride = 4 * len(fields)
        for i in range(count):
            s = o + i * stride
            src = None
            if tr_flags & 0x100:
                at = s + 4 * fields.index(0x100)
                src = struct.unpack_from(">I", buf, at)[0]
                struct.pack_into(">I", buf, at, dur)
            if tr_flags & 0x800:
                at = s + 4 * fields.index(0x800)
                if src is None:
                    src = struct.unpack_from(">I", buf, default_at)[0]
                fmt = ">i" if tr_version else ">I"
                cto = struct.unpack_from(fmt, buf, at)[0]
                struct.pack_into(fmt, buf, at, round(cto * dur / src) if src else 0)
        if not tr_flags & 0x100:
            struct.pack_into(">I", buf, default_at, dur)

        d = tfdt[0]
        if buf[d] == 1:
            struct.pack_into(">Q", buf, d + 4, self.next_time)
        else:
            struct.pack_into(">I", buf, d + 4, self.next_time & 0xFFFFFFFF)
        self.next_time += dur * count
        return True


_AVC = re.compile(r"avc[13]\.[0-9A-Fa-f]{6}")


class StreamFixer:
    """Sits in the viewer's WebSocket: fixes go2rtc's MSE answer and every fragment.

    go2rtc sends its answer (`{"type":"mse","value":"video/mp4; codecs=..."}`)
    just before the init segment. The answer is held until the init segment
    arrives, so its codec can be corrected first.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self.clock = clock
        self.held: str | None = None
        self.retimer: Retimer | None = None
        self.track_id: int | None = None
        self.off = False

    def text(self, message: str) -> list[str | bytes]:
        if self.off or self.retimer is not None or self.held is not None:
            return [message]
        try:
            msg = json.loads(message)
        except ValueError:
            return [message]
        if isinstance(msg, dict) and msg.get("type") == "mse":
            self.held = message
            return []
        return [message]

    def binary(self, data: bytes) -> list[str | bytes]:
        if self.off:
            return [data]
        try:
            return self._binary(data)
        except Exception:  # never let a parsing bug cut the video
            log.exception("camera stream: couldn't fix a fragment; passing the stream on as it is")
            self.off = True
            out: list[str | bytes] = [self.held] if self.held else []
            self.held = None
            return [*out, data]

    def _binary(self, data: bytes) -> list[str | bytes]:
        out: list[str | bytes] = []
        if self.retimer is None and self.held is not None and _first(data, [b"moov"]):
            self.held = self._fix_answer(self.held, data)
            track = video_track(data)
            if track:
                self.track_id, timescale = track
                self.retimer = Retimer(timescale, self.clock)
            else:
                log.info("camera stream: not a single video track; timestamps left as they are")
                self.off = True
        if self.held is not None:
            out.append(self.held)
            self.held = None
        if self.retimer is None or self.off:
            return [*out, data]
        moofs = [(p, e) for kind, _s, p, e in boxes(data) if kind == b"moof"]
        if not moofs:
            return [*out, data]
        buf = bytearray(data)
        for p, e in moofs:
            if not self.retimer.fragment(buf, p, e):
                log.info("camera stream: fragment layout not understood; timestamps left as they are")
                self.off = True
                return [*out, data]
        return [*out, bytes(buf)]

    @staticmethod
    def _fix_answer(answer: str, init: bytes) -> str:
        codec = avc_codec(init)
        if not codec:
            return answer
        msg = json.loads(answer)
        value = msg.get("value")
        if not isinstance(value, str) or not _AVC.search(value):
            return answer
        msg["value"] = _AVC.sub(codec, value, count=1)
        return json.dumps(msg)
