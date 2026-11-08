"""Synthetic coverage for bounded FL Studio playlist-to-pattern relationships."""

from __future__ import annotations

import struct
import zipfile

import pytest

from fl_studio_mcp.utils.playlist_graph import inspect_playlist_graph
from fl_studio_mcp.utils.project_formats import inspect_fl_studio_file


def data_event(event_id: int, payload: bytes) -> bytes:
    """Encode one FL Studio variable-length event for test fixtures."""
    n = len(payload)
    encoded = bytearray()
    while n >= 128:
        encoded.append((n & 0x7F) | 0x80)
        n >>= 7
    encoded.append(n)
    return bytes([event_id]) + bytes(encoded) + payload


def clip(
    pattern_or_channel: int,
    *,
    position: int = 0,
    duration: int = 384,
    track_reversed: int = 499,
    group: int = 0,
    stride: int = 60,
) -> bytes:
    """Construct only the documented, non-proprietary playlist clip fields."""
    base = struct.pack(
        "<IHHIHH2sH4sff",
        position,
        20480,
        pattern_or_channel,
        duration,
        track_reversed,
        group,
        b"\x78\x00",
        64,
        b"\x40\x64\x80\x80",
        0.0,
        0.0,
    )
    assert len(base) == 32
    return base + (b"\x00" * 28 if stride == 60 else b"")


def note(channel: int, pitch: int) -> bytes:
    """Return one documented 24-byte note fixture for a rack-channel ID."""
    return struct.pack("<IHHIHH8B", 0, 0, channel, 96, pitch, 0, 120, 0, 64, 0, 64, 100, 128, 128)


def flp(stream: bytes, *, kind: int = 0) -> bytes:
    """Wrap event data as an FLhd/FLdt stream without requiring the DAW."""
    return (
        b"FLhd"
        + struct.pack("<IHHH", 6, kind, 6, 96)
        + b"FLdt"
        + struct.pack("<I", len(stream))
        + stream
    )


def modern_events() -> bytes:
    """Include pattern 4 and its note before two playlist clip references."""
    return b"".join(
        (
            data_event(199, b"24.2\x00"),
            bytes([65]) + struct.pack("<H", 4),
            data_event(224, note(3, 65) + note(5, 67)),
            bytes([99]) + struct.pack("<H", 0),
            data_event(233, clip(20484) + clip(2, position=384, track_reversed=498)),
            data_event(238, b"x") * 500,
        )
    )


def test_modern_pattern_clip_and_channel_clip_link_to_notes():
    """Separate pattern and audio/automation channels; preserve encoded IDs."""
    result = inspect_playlist_graph(modern_events(), ppq=96, fl_version="24.2")
    assert result["arrangement_count"] == 1
    assert result["total_clips"] == result["clips_returned"] == 2
    assert result["playlist_record_formats"] == [{"record_bytes": 60, "blocks": 1}]
    arr = result["arrangements"][0]
    assert arr["arrangement_id"] == 0
    assert arr["track_data_events"] == 500
    pattern, channel = arr["clips"]
    assert pattern["kind"] == "pattern"
    assert pattern["pattern_id"] == 4
    assert pattern["playlist_track_number"] == 1
    assert pattern["pattern_note_count"] == 2
    assert pattern["pattern_channel_note_counts"] == [
        {"channel_id": 3, "note_count": 1},
        {"channel_id": 5, "note_count": 1},
    ]
    assert channel["kind"] == "channel"
    assert channel["channel_id"] == 2
    assert channel["playlist_track_number"] == 2
    assert "pattern_id" not in channel


def test_legacy_32_byte_and_unknown_clip_blocks():
    """Old layout decodes, but a nonstandard unknown block is not guessed."""
    raw = (
        bytes([99])
        + struct.pack("<H", 1)
        + data_event(233, clip(20489, stride=32))
        + data_event(233, b"unrecognized")
        + data_event(238, b"x") * 199
    )
    result = inspect_playlist_graph(raw, ppq=96, fl_version="20.9.1")
    assert result["total_clips"] == 1
    assert result["unrecognized_playlist_blocks"] == 1
    assert result["playlist_record_formats"] == [{"record_bytes": 32, "blocks": 1}]
    pattern = result["arrangements"][0]["clips"][0]
    assert pattern["pattern_id"] == 9
    assert pattern["playlist_track_number"] is None  # reverse index 499 > 198


def test_multiple_arrangements_and_clip_limit():
    """Bound returned clips globally across arrangements without hiding totals."""
    raw = (
        bytes([99, 0, 0])
        + data_event(233, clip(20481))
        + bytes([99, 1, 0])
        + data_event(233, clip(20482) + clip(20483))
    )
    result = inspect_playlist_graph(raw, ppq=96, max_clips=1)
    assert result["arrangement_count"] == 2
    assert result["total_clips"] == 3
    assert result["clips_returned"] == 1
    assert result["clips_truncated"] is True
    assert result["arrangements"][1]["clip_count"] == 2
    assert result["arrangements"][1]["clips"] == []


def test_full_file_and_zip_are_inspected_read_only(tmp_path):
    """A ZIP's audio assets and its source FLP must survive byte-identically."""
    original = flp(modern_events())
    project = tmp_path / "song.flp"
    project.write_bytes(original)
    output = inspect_fl_studio_file(str(project), include_playlist_graph=True)
    assert output["playlist_graph"]["total_clips"] == 2
    assert "note_graph" not in output
    assert project.read_bytes() == original

    archive = tmp_path / "session.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("song.flp", original)
        z.writestr("audio/sample.wav", b"pcm")
    result = inspect_fl_studio_file(str(archive), include_playlist_graph=True)
    assert result["projects"][0]["playlist_graph"]["total_clips"] == 2
    with zipfile.ZipFile(archive) as z:
        assert z.read("audio/sample.wav") == b"pcm"


def test_non_project_and_argument_gates(tmp_path):
    """Presets do not pretend to contain playlist arrangements."""
    preset = tmp_path / "synth.fst"
    preset.write_bytes(flp(modern_events(), kind=48))
    result = inspect_fl_studio_file(str(preset), include_playlist_graph=True)
    assert "playlist_graph" not in result
    assert result["playlist_graph_skipped"] == "Not a project container"
    for bad in (-1, 2049, True, "4"):
        with pytest.raises(ValueError, match="max_clips"):
            inspect_fl_studio_file(str(preset), max_clips=bad)
    with pytest.raises(ValueError, match="boolean"):
        inspect_fl_studio_file(str(preset), include_playlist_graph="yes")


def test_wrapper_forwards_playlist_flag(tmp_path):
    """MCP wrapper's graph selection is available without a running DAW."""
    from fl_studio_mcp.tools.project_files import register_project_file_tools

    class Recorder:
        def __init__(self):
            self.tools = {}

        def tool(self):
            def register(fn):
                self.tools[fn.__name__] = fn
                return fn

            return register

    recorder = Recorder()
    register_project_file_tools(recorder)
    file = tmp_path / "song.flp"
    file.write_bytes(flp(modern_events()))
    result = recorder.tools["fl_inspect_native_file"](
        str(file), include_playlist_graph=True, max_clips=1
    )
    assert result["playlist_graph"]["total_clips"] == 2
    assert result["playlist_graph"]["clips_returned"] == 1


def test_clip_offsets_with_nan_sentinel_do_not_hide_real_playlist_data():
    """FL Studio sometimes stores unset clip offsets as 0xFFFFFFFF bytes."""
    record = bytearray(clip(20484))
    record[24:32] = b"\xff" * 8
    stream = bytes([99, 0, 0]) + data_event(233, bytes(record))
    result = inspect_playlist_graph(stream, ppq=96, fl_version="24.2")
    assert result["total_clips"] == 1
    assert result["unrecognized_playlist_blocks"] == 0
    assert result["arrangements"][0]["clips"][0]["pattern_id"] == 4


def test_ambiguous_record_stride_remains_unrecognized_without_version():
    """A mixed/unknown layout must not be given an invented interpretation."""
    stream = data_event(233, b"\x00" * 480)
    result = inspect_playlist_graph(stream, ppq=96)
    assert result["total_clips"] == 0
    assert result["unrecognized_playlist_blocks"] == 1
