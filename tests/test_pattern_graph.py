"""Read-only pattern-note graph tests with synthetic FL Studio event streams."""

from __future__ import annotations

import struct
import zipfile

import pytest

from fl_studio_mcp.utils.pattern_graph import inspect_pattern_note_graph
from fl_studio_mcp.utils.project_formats import inspect_fl_studio_file


def event_data(event_id: int, body: bytes) -> bytes:
    length = len(body)
    if length >= 128:
        return bytes([event_id, length & 127 | 128, length >> 7]) + body
    return bytes([event_id, length]) + body


def note(channel: int, pitch: int, start: int, length: int) -> bytes:
    return struct.pack(
        "<IHHIHH8B",
        start,
        0,
        channel,
        length,
        pitch,
        0,
        120,
        0,
        64,
        0,
        64,
        100,
        128,
        128,
    )


def events() -> bytes:
    return b"".join(
        (
            bytes([65]) + struct.pack("<H", 1),
            event_data(224, note(2, 60, 0, 96) + note(3, 64, 96, 48)),
            bytes([65]) + struct.pack("<H", 2),
            event_data(224, note(2, 67, 0, 192)),
            bytes([65]) + struct.pack("<H", 1),  # metadata re-mentions same pattern
        )
    )


def flp(stream: bytes) -> bytes:
    return (
        b"FLhd"
        + struct.pack("<IHHH", 6, 0, 4, 96)
        + b"FLdt"
        + struct.pack("<I", len(stream))
        + stream
    )


def test_patterns_and_note_channel_relationships():
    result = inspect_pattern_note_graph(events(), ppq=96)
    assert result["pattern_count"] == 2
    assert result["total_note_count"] == 3
    one, two = result["patterns"]
    assert one["pattern_id"] == 1
    assert one["note_count"] == 2
    assert one["channel_note_counts"] == [
        {"channel_id": 2, "note_count": 1},
        {"channel_id": 3, "note_count": 1},
    ]
    assert one["notes"][0] == {
        "channel_id": 2,
        "pitch": 60,
        "position_ticks": 0,
        "length_ticks": 96,
        "velocity_raw": 100,
        "flags": 0,
    }
    assert two["pattern_id"] == 2
    assert two["notes"][0]["pitch"] == 67
    assert result["notes_truncated"] is False


def test_graph_note_limit_and_validation():
    result = inspect_pattern_note_graph(events(), ppq=96, max_notes=1)
    assert result["total_note_count"] == 3
    assert result["notes_returned"] == 1
    assert result["notes_truncated"] is True
    for invalid in (-1, 4097, True, "1"):
        with pytest.raises(ValueError):
            inspect_pattern_note_graph(events(), ppq=96, max_notes=invalid)
    stream = bytes([65, 1, 0]) + event_data(224, b"x") + event_data(224, note(4, 60, 0, 96))
    recovered = inspect_pattern_note_graph(stream, ppq=96)
    assert recovered["note_blocks_unrecognized_layout"] == 1
    assert recovered["total_note_count"] == 1
    assert recovered["notes_returned"] == 1
    assert recovered["patterns"][0]["notes"][0]["channel_id"] == 4


def test_project_and_zip_optional_graph_inspection(tmp_path):
    project = tmp_path / "test.flp"
    original = flp(events())
    project.write_bytes(original)
    without_graph = inspect_fl_studio_file(str(project))
    assert "note_graph" not in without_graph
    with_graph = inspect_fl_studio_file(str(project), include_note_graph=True)
    assert with_graph["note_graph"]["total_note_count"] == 3
    assert project.read_bytes() == original

    archive = tmp_path / "song.zip"
    with zipfile.ZipFile(archive, "w") as file:
        file.writestr("session.flp", original)
        file.writestr("sample.wav", b"PCM-sample")
    result = inspect_fl_studio_file(str(archive), include_note_graph=True)
    assert result["projects"][0]["note_graph"]["pattern_count"] == 2
    with zipfile.ZipFile(archive) as file:
        assert file.read("sample.wav") == b"PCM-sample"


def test_pattern_limit_counts_unique_ignored_ids_only():
    stream = b"".join(
        bytes([65]) + struct.pack("<H", pattern_id) for pattern_id in (*range(1, 259), 258, 258)
    )
    result = inspect_pattern_note_graph(stream, ppq=96, max_notes=0)
    assert result["pattern_count"] == 256
    assert result["patterns_over_limit"] == 2
    assert result["total_note_count"] == 0


def test_optional_graph_rejects_invalid_arguments(tmp_path):
    project = tmp_path / "song.flp"
    project.write_bytes(flp(events()))
    with pytest.raises(ValueError, match="boolean"):
        inspect_fl_studio_file(str(project), include_note_graph="yes")
    with pytest.raises(ValueError, match="max_notes"):
        inspect_fl_studio_file(str(project), include_note_graph=True, max_notes=5000)


def test_orphan_note_records_are_not_reported_as_output_truncation():
    """Orphan notes count toward totals but cannot appear in a pattern's returned notes."""
    stream = event_data(224, note(7, 70, 0, 96)) + events()
    result = inspect_pattern_note_graph(stream, ppq=96, max_notes=20)
    assert result["total_note_count"] == 4
    assert result["unattributed_note_count"] == 1
    assert result["notes_returned"] == 3
    assert result["notes_truncated"] is False


def test_malformed_note_block_keeps_container_inventory(tmp_path):
    """Graph opt-in must not degrade a validly framed project to an error."""
    stream = events() + event_data(224, b"unknown-layout")
    file = tmp_path / "song.flp"
    original = flp(stream)
    file.write_bytes(original)
    result = inspect_fl_studio_file(str(file), include_note_graph=True)
    assert result["valid_framing"] is True
    assert result["event_summary"]["note_blocks_unrecognized_layout"] == 1
    assert result["note_graph"]["note_blocks_unrecognized_layout"] == 1
    assert result["note_graph"]["total_note_count"] == 3
    assert file.read_bytes() == original


def test_fst_explicitly_reports_graph_skip(tmp_path):
    """An FST plugin-state blob is not silently claimed to be a project graph."""
    content = bytearray(flp(events()))
    struct.pack_into("<H", content, 8, 48)
    file = tmp_path / "synth.fst"
    file.write_bytes(content)
    result = inspect_fl_studio_file(str(file), include_note_graph=True)
    assert result["format_name"] == "plugin_state"
    assert "note_graph" not in result
    assert result["note_graph_skipped"] == "Not a project or score container"
