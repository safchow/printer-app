import json
import struct

from app.fmp4 import MAX_HOLD, StreamFixer, avc_codec, boxes, find, parse_sps, rebuild_init, video_track

TIMESCALE = 90000
# A real H.264 Main 3.0, 800x600 SPS and its PPS (from x264), and go2rtc-style
# placeholder ones (Baseline 1.0, 128x96).
SPS = bytes.fromhex("674d401ed900c8137e5c044000000301400000030383c58b92")
PPS = bytes.fromhex("68ebc3cb20")
PLACEHOLDER_SPS = bytes.fromhex("6742c00ad9020db011000003000100000300140f122648")
PLACEHOLDER_PPS = bytes.fromhex("68cb83cb20")


def box(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", 8 + len(payload)) + kind + payload


def full(kind: bytes, version: int, flags: int, payload: bytes) -> bytes:
    return box(kind, bytes([version]) + flags.to_bytes(3, "big") + payload)


def nals(*units: bytes) -> bytes:
    return b"".join(struct.pack(">I", len(u)) + u for u in units)


def init_segment(sps=PLACEHOLDER_SPS, pps=PLACEHOLDER_PPS, handler=b"vide", tracks=1) -> bytes:
    avcc = box(b"avcC", bytes([1, *sps[1:4], 0xFF, 0xE1]) + struct.pack(">H", len(sps)) + sps
               + b"\x01" + struct.pack(">H", len(pps)) + pps)
    avc1 = box(b"avc1", bytes(78) + avcc)
    stsd = full(b"stsd", 0, 0, struct.pack(">I", 1) + avc1)
    stbl = box(b"stbl", stsd)
    minf = box(b"minf", stbl)
    mdhd = full(b"mdhd", 0, 0, struct.pack(">IIII", 0, 0, TIMESCALE, 0) + bytes(4))
    hdlr = full(b"hdlr", 0, 0, bytes(4) + handler + bytes(13))
    tkhd = full(b"tkhd", 0, 3, struct.pack(">IIII", 0, 0, 1, 0) + bytes(64))
    trak = box(b"trak", tkhd + box(b"mdia", mdhd + hdlr + minf))
    return box(b"ftyp", b"iso5\x00\x00\x02\x00iso5iso6mp41") + box(b"moov", trak * tracks)


def fragment(seq: int, tfdt: int, duration: int = 64300, cto: int = 0, keyframe: bool | None = None) -> bytes:
    """Like go2rtc's: one sample, every per-sample field in trun, tfdt version 1."""
    mfhd = full(b"mfhd", 0, 0, struct.pack(">I", seq))
    tfhd = full(b"tfhd", 0, 0x20000, struct.pack(">I", 1))
    tfdt_box = full(b"tfdt", 1, 0, struct.pack(">Q", tfdt))
    trun = full(b"trun", 0, 0xF01, struct.pack(">Ii", 1, 0) + struct.pack(">IIII", duration, 4, 0, cto))
    moof = box(b"moof", mfhd + box(b"traf", tfhd + tfdt_box + trun))
    keyframe = seq == 1 if keyframe is None else keyframe
    frame = nals(SPS, PPS, b"\x65\x88\x84") if keyframe else nals(b"\x41\x9a\x02")
    return moof + box(b"mdat", frame)


def times(data: bytes) -> tuple[int, int, int]:
    """(tfdt, sample duration, composition offset) of a go2rtc-style fragment."""
    (p, _e), = find(data, [b"moof", b"traf", b"tfdt"])
    (t, _e), = find(data, [b"moof", b"traf", b"trun"])
    dur, _size, _flags, cto = struct.unpack_from(">IIII", data, t + 12)
    return struct.unpack_from(">Q", data, p + 4)[0], dur, cto


ANSWER = json.dumps({"type": "mse", "value": 'video/mp4; codecs="avc1.640029"'})


def run(fixer: StreamFixer, clock: list[float], gaps: list[float], start: int = 0):
    out = []
    for i, gap in enumerate(gaps):
        clock[0] += gap
        out += fixer.binary(fragment(i + 1, start + i * 64300))
    return [m for m in out if isinstance(m, bytes) and m[4:8] == b"moof"]


def test_reads_the_codec_track_and_sps():
    init = init_segment(SPS, PPS)
    assert avc_codec(init) == "avc1.4D401E"
    assert avc_codec(init_segment()) == "avc1.42C00A"
    assert parse_sps(SPS)["width"] == 800 and parse_sps(SPS)["height"] == 600
    assert parse_sps(PLACEHOLDER_SPS)["width"] == 128
    assert video_track(init) == (1, TIMESCALE)
    assert video_track(init_segment(handler=b"soun")) is None
    assert video_track(init_segment(tracks=2)) is None
    assert [b[0] for b in boxes(init)] == [b"ftyp", b"moov"]


def test_init_is_rebuilt_from_the_first_keyframe():
    fixer = StreamFixer(clock=lambda: 0.0)
    assert fixer.text(ANSWER) == []
    assert fixer.binary(init_segment()) == []  # held for the real SPS/PPS
    answer, init, first = fixer.binary(fragment(1, 0))
    assert json.loads(answer)["value"] == 'video/mp4; codecs="avc1.4D401E"'
    assert init == rebuild_init(init_segment(), SPS, PPS)
    stsd = next(find(init, [b"moov", b"trak", b"mdia", b"minf", b"stbl", b"stsd"]))
    (_k, _s, ep, ee), = list(boxes(init, stsd[0] + 8, stsd[1]))
    assert struct.unpack_from(">HH", init, ep + 24) == (800, 600)
    tkhd = next(find(init, [b"moov", b"trak", b"tkhd"]))
    assert struct.unpack_from(">II", init, tkhd[1] - 8) == (800 << 16, 600 << 16)
    assert video_track(init) == (1, TIMESCALE)
    assert times(first)[0] == 0
    assert fixer.binary(fragment(2, 64300)) != []  # then fragments flow straight through


def test_init_goes_out_unchanged_if_no_sps_comes():
    fixer = StreamFixer(clock=lambda: 0.0)
    fixer.text(ANSWER)
    init = init_segment()
    fixer.binary(init)
    out = []
    for i in range(MAX_HOLD):
        out += fixer.binary(fragment(i + 2, i, keyframe=False))
    assert out[0] == json.dumps({"type": "mse", "value": 'video/mp4; codecs="avc1.42C00A"'})
    assert out[1] == init
    assert len(out) == 2 + MAX_HOLD


def test_other_messages_pass_straight_through():
    fixer = StreamFixer(clock=lambda: 0.0)
    error = json.dumps({"type": "error", "value": "nope"})
    assert fixer.text(error) == [error]
    assert fixer.text("not json") == ["not json"]


def test_fragments_get_real_time_end_to_end():
    clock = [100.0]
    fixer = StreamFixer(clock=lambda: clock[0])
    fixer.text(ANSWER)
    fixer.binary(init_segment())
    # Frames arrive 1/14 s apart, but say 0.71 s each (the K1C's clock).
    out = run(fixer, clock, [1 / 14] * 40, start=5_000_000)
    t = [times(f) for f in out]
    assert t[0][0] == 0  # the timeline starts at zero
    for (a, d, _), (b, _, _) in zip(t, t[1:]):
        assert b == a + d  # no gaps, no overlaps
    assert abs(t[-1][1] / TIMESCALE - 1 / 14) < 0.002  # settled on the real frame time
    assert sum(d for _, d, _ in t) / TIMESCALE < 4  # 40 frames ≈ 3 s, not 28 s


def test_composition_offsets_scale_with_the_duration():
    clock = [0.0]
    fixer = StreamFixer(clock=lambda: clock[0])
    fixer.text(ANSWER)
    fixer.binary(init_segment())
    clock[0] += 0.1
    *_, out = fixer.binary(fragment(1, 0, duration=64300, cto=128600))
    _, dur, cto = times(out)
    assert cto == 2 * dur


def test_bursts_are_smoothed_and_clamped():
    clock = [0.0]
    fixer = StreamFixer(clock=lambda: clock[0])
    fixer.text(ANSWER)
    fixer.binary(init_segment())
    out = run(fixer, clock, [1 / 14] * 20 + [0.0] * 5 + [5.0])
    durs = [times(f)[1] / TIMESCALE for f in out]
    assert all(1 / 60 - 1e-6 <= d <= 1.0 for d in durs)
    assert durs[24] > 1 / 30  # five frames at once don't collapse the frame time


def test_unknown_layouts_are_left_alone():
    fixer = StreamFixer(clock=lambda: 0.0)
    fixer.text(ANSWER)
    out = fixer.binary(b"\x00init")  # no moov: answer goes out unchanged, then the data
    assert out == [ANSWER, b"\x00init"]
    assert fixer.binary(b"\x00segment") == [b"\x00segment"]

    fixer = StreamFixer(clock=lambda: 0.0)
    fixer.text(ANSWER)
    fixer.binary(init_segment(tracks=2))
    frag = fragment(1, 123)
    assert fixer.binary(frag) == [frag]
