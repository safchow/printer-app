import json
import struct

from app.fmp4 import StreamFixer, avc_codec, boxes, find, video_track

TIMESCALE = 90000


def box(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", 8 + len(payload)) + kind + payload


def full(kind: bytes, version: int, flags: int, payload: bytes) -> bytes:
    return box(kind, bytes([version]) + flags.to_bytes(3, "big") + payload)


def init_segment(profile=(0x4D, 0x40, 0x1E), handler=b"vide", tracks=1) -> bytes:
    avcc = box(b"avcC", bytes([1, *profile, 0xFF, 0xE1]))
    avc1 = box(b"avc1", bytes(78) + avcc)
    stsd = full(b"stsd", 0, 0, struct.pack(">I", 1) + avc1)
    stbl = box(b"stbl", stsd)
    minf = box(b"minf", stbl)
    mdhd = full(b"mdhd", 0, 0, struct.pack(">IIII", 0, 0, TIMESCALE, 0) + bytes(4))
    hdlr = full(b"hdlr", 0, 0, bytes(4) + handler + bytes(13))
    tkhd = full(b"tkhd", 0, 3, struct.pack(">IIII", 0, 0, 1, 0) + bytes(64))
    trak = box(b"trak", tkhd + box(b"mdia", mdhd + hdlr + minf))
    return box(b"ftyp", b"iso5\x00\x00\x02\x00iso5iso6mp41") + box(b"moov", trak * tracks)


def fragment(seq: int, tfdt: int, duration: int = 64300, cto: int = 0) -> bytes:
    """Like go2rtc's: one sample, every per-sample field in trun, tfdt version 1."""
    mfhd = full(b"mfhd", 0, 0, struct.pack(">I", seq))
    tfhd = full(b"tfhd", 0, 0x20000, struct.pack(">I", 1))
    tfdt_box = full(b"tfdt", 1, 0, struct.pack(">Q", tfdt))
    trun = full(b"trun", 0, 0xF01, struct.pack(">Ii", 1, 0) + struct.pack(">IIII", duration, 4, 0, cto))
    moof = box(b"moof", mfhd + box(b"traf", tfhd + tfdt_box + trun))
    return moof + box(b"mdat", b"\x00\x00\x00\x01")


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
    return out


def test_reads_the_real_codec_and_track():
    init = init_segment()
    assert avc_codec(init) == "avc1.4D401E"
    assert video_track(init) == (1, TIMESCALE)
    assert video_track(init_segment(handler=b"soun")) is None
    assert video_track(init_segment(tracks=2)) is None
    assert [b[0] for b in boxes(init)] == [b"ftyp", b"moov"]


def test_answer_waits_for_the_init_segment_and_gets_the_right_codec():
    fixer = StreamFixer(clock=lambda: 0.0)
    assert fixer.text(ANSWER) == []
    init = init_segment()
    answer, segment = fixer.binary(init)
    assert json.loads(answer)["value"] == 'video/mp4; codecs="avc1.4D401E"'
    assert segment == init


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
    (out,) = fixer.binary(fragment(1, 0, duration=64300, cto=128600))
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
