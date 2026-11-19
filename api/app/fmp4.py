"""Fixes for the fragmented MP4 that go2rtc makes from the K1C's camera.

Three things are wrong with it, and iPhone Safari refuses the stream because
of them (desktop Chrome plays it anyway):

* **The init segment describes the wrong video.** go2rtc writes it before it
  has seen any video, so its avcC box is a placeholder (Baseline 1.0, 128x96)
  while the K1C sends Main 3.0 at 800x600, with the real SPS/PPS in-band on
  keyframes. Safari sets up its decoder from avcC. We hold the init segment
  until the first fragment with an SPS and PPS, and rebuild avcC, and the
  width and height in avc1 and tkhd, from them.
* **The codec label is wrong**, for the same reason (go2rtc has said
  `avc1.640029` and `avc1.42000A`). The answer to the page gets the codec of
  the rebuilt avcC.
* **The clock runs about ten times too fast.** Each fragment holds one frame
  and they arrive about 14 times a second, but their timestamps say 0.7 s per
  frame. The buffer races ahead of the playhead. We give every frame a
  duration measured from when fragments actually arrive (averaged over the
  last couple of seconds), and lay them end to end from zero: no gaps, no
  overlaps, real speed.

Fragments keep their size: only fields are rewritten in place. Only box fields are rewritten, in place, so every box keeps its size. If
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



# --- the real SPS/PPS -------------------------------------------------------------
#
# go2rtc writes the init segment before it has seen the K1C's video: its avcC
# box is a placeholder (Baseline 1.0, 128x96, from the SDP), while the stream
# is Main 3.0 at 800x600 with the real SPS/PPS sent in-band on keyframes.
# Safari sets up its decoder from avcC, so we hold the init segment until the
# first fragment that carries an SPS and a PPS, and rebuild avcC (plus the
# width and height in avc1 and tkhd) from them.

MAX_HOLD = 60  # fragments held while waiting for an SPS/PPS before giving up


class _Bits:
    def __init__(self, data: bytes):
        self.data, self.pos = data, 0

    def u(self, n: int) -> int:
        v = 0
        for _ in range(n):
            byte = self.data[self.pos >> 3]  # IndexError on a short SPS: caller gives up
            v = (v << 1) | ((byte >> (7 - (self.pos & 7))) & 1)
            self.pos += 1
        return v

    def ue(self) -> int:
        zeros = 0
        while self.u(1) == 0:
            zeros += 1
            if zeros > 31:
                raise ValueError("bad exp-Golomb code")
        return (1 << zeros) - 1 + self.u(zeros)

    def se(self) -> int:
        k = self.ue()
        return (k + 1) // 2 if k & 1 else -(k // 2)


def _rbsp(nal: bytes) -> bytes:
    return re.sub(b"\x00\x00\x03", b"\x00\x00", nal)


def parse_sps(sps: bytes) -> dict:
    """Width, height and chroma details from an H.264 SPS (NAL header included)."""
    b = _Bits(_rbsp(sps[1:]))
    profile = b.u(8)
    b.u(16)  # constraint flags, level
    b.ue()  # sps id
    chroma, depth_luma, depth_chroma = 1, 8, 8
    if profile in (100, 110, 122, 244, 44, 83, 86, 118, 128, 138, 139, 134, 135):
        chroma = b.ue()
        if chroma == 3:
            b.u(1)
        depth_luma, depth_chroma = b.ue() + 8, b.ue() + 8
        b.u(1)
        if b.u(1):  # scaling matrices
            for i in range(12 if chroma == 3 else 8):
                if b.u(1):
                    last = nxt = 8
                    for _ in range(16 if i < 6 else 64):
                        if nxt:
                            nxt = (last + b.se() + 256) % 256
                        last = nxt or last
    b.ue()  # log2_max_frame_num
    poc = b.ue()
    if poc == 0:
        b.ue()
    elif poc == 1:
        b.u(1)
        b.se()
        b.se()
        for _ in range(b.ue()):
            b.se()
    b.ue()  # max_num_ref_frames
    b.u(1)
    w_mbs, h_map = b.ue() + 1, b.ue() + 1
    frame_mbs_only = b.u(1)
    if not frame_mbs_only:
        b.u(1)
    b.u(1)
    width, height = w_mbs * 16, (2 - frame_mbs_only) * h_map * 16
    if b.u(1):  # cropping
        left, right, top, bottom = b.ue(), b.ue(), b.ue(), b.ue()
        cx = 1 if chroma in (0, 3) else 2
        cy = (2 if chroma == 1 else 1) * (2 - frame_mbs_only)
        width -= (left + right) * cx
        height -= (top + bottom) * cy
    return {"profile": profile, "width": width, "height": height,
            "chroma": chroma, "depth_luma": depth_luma, "depth_chroma": depth_chroma}


def nal_units(data: bytes, start: int, end: int, length_size: int = 4):
    """The NAL units in a length-prefixed run (an mdat's payload)."""
    i = start
    while i + length_size <= end:
        n = int.from_bytes(data[i:i + length_size], "big")
        i += length_size
        if n == 0 or i + n > end:
            return
        yield data[i:i + n]
        i += n


def parameter_sets(fragment: bytes, length_size: int = 4) -> tuple[bytes, bytes] | None:
    """The first SPS and PPS in a fragment's mdat, if it has both."""
    sps = pps = None
    for kind, _s, p, e in boxes(fragment):
        if kind != b"mdat":
            continue
        for nal in nal_units(fragment, p, e, length_size):
            t = nal[0] & 0x1F
            if t == 7 and sps is None:
                sps = bytes(nal)
            elif t == 8 and pps is None:
                pps = bytes(nal)
    return (sps, pps) if sps and pps else None


def length_size(init: bytes) -> int:
    for p, e in find(init, [b"moov", b"trak", b"mdia", b"minf", b"stbl", b"stsd"]):
        for kind, _s, ep, ee in boxes(init, p + 8, e):
            if kind in (b"avc1", b"avc3"):
                avcc = _first(init, [b"avcC"], ep + 78, ee)
                if avcc and avcc[1] - avcc[0] >= 5:
                    return (init[avcc[0] + 4] & 3) + 1
    return 4


def make_avcc(sps: bytes, pps: bytes, info: dict, nal_length: int = 4) -> bytes:
    body = bytes([1, sps[1], sps[2], sps[3], 0xFC | (nal_length - 1), 0xE1])
    body += struct.pack(">H", len(sps)) + sps + b"\x01" + struct.pack(">H", len(pps)) + pps
    if info["profile"] in (100, 110, 122, 144):
        body += bytes([0xFC | info["chroma"], 0xF8 | (info["depth_luma"] - 8), 0xF8 | (info["depth_chroma"] - 8), 0])
    return body


def _box(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", 8 + len(payload)) + kind + payload


def rebuild_init(init: bytes, sps: bytes, pps: bytes) -> bytes:
    """The init segment with avcC built from the real SPS/PPS, and the real width and height."""
    info = parse_sps(sps)
    avcc = make_avcc(sps, pps, info, length_size(init))
    w, h = info["width"], info["height"]

    def walk(start: int, end: int) -> bytes:
        out = b""
        for kind, s, p, e in boxes(init, start, end):
            if kind in _CONTAINERS:
                out += _box(kind, walk(p, e))
            elif kind == b"stsd":
                out += _box(kind, init[p:p + 8] + walk(p + 8, e))
            elif kind in (b"avc1", b"avc3"):
                entry = bytearray(init[p:p + 78])
                struct.pack_into(">HH", entry, 24, w, h)
                out += _box(kind, bytes(entry) + walk(p + 78, e))
            elif kind == b"avcC":
                out += _box(kind, avcc)
            elif kind == b"tkhd" and e - p >= 8:
                body = bytearray(init[p:e])
                struct.pack_into(">II", body, len(body) - 8, w << 16, h << 16)
                out += _box(kind, bytes(body))
            else:
                out += init[s:e]
        return out

    return walk(0, len(init))


_AVC = re.compile(r"avc[13]\.[0-9A-Fa-f]{6}")


class StreamFixer:
    """Sits in the viewer's WebSocket: fixes go2rtc's MSE answer, init segment and fragments.

    go2rtc sends its answer (`{"type":"mse","value":"video/mp4; codecs=..."}`),
    then the init segment, then fragments. The answer and init segment are
    held until a fragment brings the real SPS/PPS (normally the first, which
    starts on a keyframe); then the corrected answer and init segment go out,
    followed by the held fragments. Fragments are retimed as they arrive.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self.clock = clock
        self.answer: str | None = None
        self.init: bytes | None = None
        self.waiting: list[bytes] = []  # fragments held until the SPS/PPS turns up
        self.retimer: Retimer | None = None
        self.nal_length = 4
        self.started = False  # the answer and init segment have gone out
        self.off = False

    def text(self, message: str) -> list[str | bytes]:
        if self.off or self.started or self.answer is not None:
            return [message]
        try:
            msg = json.loads(message)
        except ValueError:
            return [message]
        if isinstance(msg, dict) and msg.get("type") == "mse":
            self.answer = message
            return []
        return [message]

    def binary(self, data: bytes) -> list[str | bytes]:
        if self.off:
            return [data]
        try:
            return self._binary(data)
        except Exception:  # never let a parsing bug cut the video
            log.exception("camera stream: couldn't fix the stream; passing it on as it is")
            return self._give_up(data)

    def _give_up(self, data: bytes | None = None) -> list[str | bytes]:
        """Send whatever is held, as it came, and stop fixing."""
        self.off = True
        out: list[str | bytes] = []
        if not self.started:
            if self.answer is not None:
                out.append(self.answer)
            if self.init is not None:
                out.append(self.init)
            out += self.waiting
        self.answer, self.init, self.waiting, self.started = None, None, [], True
        return out + ([data] if data is not None else [])

    def _start(self, init: bytes) -> list[str | bytes]:
        out: list[str | bytes] = []
        if self.answer is not None:
            out.append(self._fix_answer(self.answer, init))
        out.append(init)
        out += self.waiting
        self.answer, self.init, self.waiting, self.started = None, None, [], True
        return out

    def _binary(self, data: bytes) -> list[str | bytes]:
        if not self.started and self.init is None:
            if not _first(data, [b"moov"]):
                return self._give_up(data)  # not the stream we know
            track = video_track(data)
            if not track or not avc_codec(data):
                log.info("camera stream: not a single H.264 track; passed on as it is")
                return self._give_up(data)
            self.retimer = Retimer(track[1], self.clock)
            self.nal_length = length_size(data)
            self.init = data
            return []

        data = self._retime(data)
        if data is None:
            log.info("camera stream: fragment layout not understood; passed on as it is")
            return self._give_up()
        if self.started:
            return [data]

        found = parameter_sets(data, self.nal_length)
        if found:
            init = self.init
            try:
                init = rebuild_init(self.init, *found)
            except (ValueError, IndexError, struct.error):
                log.info("camera stream: couldn't read the SPS; init segment left as it is")
            self.waiting.append(data)
            return self._start(init)
        self.waiting.append(data)
        if len(self.waiting) >= MAX_HOLD:
            log.info("camera stream: no SPS/PPS in the first fragments; init segment left as it is")
            return self._start(self.init)
        return []

    def _retime(self, data: bytes) -> bytes | None:
        moofs = [(p, e) for kind, _s, p, e in boxes(data) if kind == b"moof"]
        if not moofs:
            return data
        buf = bytearray(data)
        for p, e in moofs:
            if not self.retimer.fragment(buf, p, e):
                return None
        return bytes(buf)

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
