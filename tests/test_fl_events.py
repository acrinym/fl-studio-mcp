"""Synthetic event fixtures; never edit real FL Studio projects."""

import struct

import pytest

from fl_studio_mcp.utils.fl_events import inspect_event_stream


def variable(event_id: int, payload: bytes) -> bytes:
    length = len(payload)
    prefix = bytearray()
    while length >= 128:
        prefix.append((length & 127) | 128)
        length >>= 7
    prefix.append(length)
    return bytes([event_id]) + bytes(prefix) + payload


def test_structural_inventory_avoids_opaque_plugin_data():
    events = b"".join(
        [
            bytes([64]) + struct.pack("<H", 3),  # new channel
            bytes([65]) + struct.pack("<H", 2),  # new pattern
            bytes([66]) + struct.pack("<H", 140),  # tempo
            variable(199, b"24.2\x00"),
            variable(201, b"Sytrus\x00"),
            variable(213, b"VERY-PRIVATE-PLUGIN-PRESET"),
            variable(224, b"\x00" * 48),
        ]
    )
    result = inspect_event_stream(events)
    assert result["event_count"] == 7
    assert result["channel_new_events"] == 1
    assert result["pattern_new_events"] == 1
    assert result["coarse_tempo_bpm"] == 140
    assert result["fl_version"] == "24.2"
    assert result["note_records_24_byte_layout"] == 2
    assert result["plugin_data_events"] == 1
    assert result["opaque_plugin_data_bytes"] == 26
    assert result["observed_plugin_labels"] == ["Sytrus"]
    assert "PRIVATE" not in str(result)


def test_varint_multiple_bytes_and_bounds():
    result = inspect_event_stream(variable(213, b"X" * 300))
    assert result["opaque_plugin_data_bytes"] == 300
    with pytest.raises(ValueError, match="Truncated"):
        inspect_event_stream(bytes([224, 0x81]))
    with pytest.raises(ValueError, match="exceeds"):
        inspect_event_stream(bytes([224, 0x7F, 0x00]))
    with pytest.raises(ValueError, match="Overlong"):
        inspect_event_stream(bytes([224, 0x80, 0x80, 0x80, 0x80, 0x80]))


def test_null_stream_rejected():
    with pytest.raises(ValueError, match="NUL"):
        inspect_event_stream(b"\x00" * 20)


def test_project_only_settings_not_misattributed_to_presets():
    stream = b"".join(
        [
            bytes([9, 1]),  # loop_active
            bytes([10, 0]),  # show_info
            bytes([23, 2]),  # triangular pan law
            bytes([80]) + struct.pack("<h", -120),  # master pitch cents
            bytes([66]) + struct.pack("<H", 125),
        ]
    )
    project = inspect_event_stream(stream, format_code=0)
    assert project["project_settings"] == {
        "loop_active": True,
        "show_info": False,
        "pan_law": "triangular",
        "main_pitch_cents": -120,
        "coarse_tempo_bpm": 125,
    }
    preset = inspect_event_stream(stream, format_code=48)
    assert preset["project_settings"] is None
    assert preset["coarse_tempo_bpm"] is None
