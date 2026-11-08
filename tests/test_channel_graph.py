"""Synthetic channel identity fixtures for native FL Studio files."""

from __future__ import annotations

import struct
import zipfile

import pytest

from fl_studio_mcp.utils.channel_graph import inspect_channel_graph
from fl_studio_mcp.utils.project_formats import inspect_fl_studio_file


def text_event(event_id: int, label: str) -> bytes:
    """Encode the FL 11.5+ Unicode name events without real plugin binaries."""
    payload = (label + "\x00").encode("utf-16-le")
    size = len(payload)
    assert size < 128
    return bytes([event_id, size]) + payload


def data_event(event_id: int, payload: bytes) -> bytes:
    """Bounded, short FL Studio DATA event fixture."""
    assert len(payload) < 128
    return bytes([event_id, len(payload)]) + payload


def channel(
    channel_id: int,
    *,
    kind: int,
    display: str,
    internal: str = "",
    insert: int = 0,
    enabled: bool = True,
) -> bytes:
    """Represent channel creation, names, opaque state and direct routing."""
    return b"".join(
        (
            bytes([64]) + struct.pack("<H", channel_id),
            bytes([21, kind]),
            text_event(201, internal),
            data_event(212, b"wrapper"),
            text_event(203, display),
            bytes([0, int(enabled)]),
            bytes([22]) + struct.pack("<b", insert),
            data_event(213, b"VENDOR-PROPRIETARY-STATE"),
        )
    )


def flp(stream: bytes, *, format_code: int = 0) -> bytes:
    """Build a standalone valid FLhd/FLdt project."""
    return (
        b"FLhd"
        + struct.pack("<IHHH", 6, format_code, 3, 96)
        + b"FLdt"
        + struct.pack("<I", len(stream))
        + stream
    )


def events() -> bytes:
    """Project metadata, sampler, native instrument and unresolved VST wrapper."""
    return b"".join(
        (
            data_event(199, b"24.2\x00"),
            channel(0, kind=0, display="Kick"),
            data_event(196, b"C:\\Private\\sample.wav"),
            channel(4, kind=2, display="142-Pizzstr", internal="Soundfont Player", insert=8),
            channel(5, kind=4, display="Custom renamed lead", internal="Fruity Wrapper", insert=-1),
            bytes([99]) + struct.pack("<H", 0),
            text_event(203, "Mixer FX name MUST NOT LEAK"),
            data_event(213, b"UNRELATED-MIXER-STATE"),
        )
    )


def test_project_channel_identity_types_and_routing():
    """Only documented channel fields are exposed; plugin IDs are not guessed."""
    result = inspect_channel_graph(events(), fl_version="24.2")
    assert result["channel_count"] == 3
    sampler, native, vst = result["channels"]
    assert sampler["channel_id"] == 0
    assert sampler["channel_type"] == "sampler"
    assert sampler["display_name"] == "Kick"
    assert sampler["plugin_identity"] == "fl_studio_sampler"
    assert sampler["has_sample_reference"] is True
    assert sampler["enabled"] is True
    assert native["channel_id"] == 4
    assert native["channel_type"] == "native"
    assert native["display_name"] == "142-Pizzstr"
    assert native["internal_plugin_name"] == "Soundfont Player"
    assert native["plugin_identity"] == "native_plugin_internal_name"
    assert native["mixer_insert_index"] == 8
    assert vst["channel_type"] == "instrument"
    assert vst["display_name"] == "Custom renamed lead"
    assert vst["internal_plugin_name"] == "Fruity Wrapper"
    assert vst["plugin_identity"] == "third_party_wrapper_unresolved"
    assert vst["mixer_insert_index"] == -1
    assert all(ch["opaque_plugin_state_bytes"] == 24 for ch in result["channels"])
    assert "Private" not in str(result)
    assert "VENDOR" not in str(result)
    assert "Mixer FX" not in str(result)


def test_duplicate_id_cannot_override_first_channel():
    """Repeated/new channel IDs are flagged, not overwritten by later names."""
    raw = channel(4, kind=2, display="First", internal="Sytrus") + channel(
        4, kind=4, display="Malicious overwrite", internal="Fruity Wrapper"
    )
    graph = inspect_channel_graph(raw, fl_version="24.0")
    assert graph["channel_count"] == 1
    assert graph["duplicate_channel_ids"] == 1
    assert graph["channels"][0]["display_name"] == "First"


def test_malformed_unicode_name_is_not_exposed():
    """Unknown/invalid labels do not become arbitrary decoded byte fragments."""
    raw = bytes([64, 1, 0, 21, 2]) + bytes([203, 3, 0x41, 0, 0x42])
    graph = inspect_channel_graph(raw, fl_version="24.0")
    assert graph["channels"][0]["display_name"] is None


def test_fst_plugin_state_skips_channel_graph_and_zip_is_read_only(tmp_path):
    """Preset format distinction and ZIP read-only behavior are preserved."""
    preset = tmp_path / "my.fst"
    preset.write_bytes(flp(events(), format_code=48))
    result = inspect_fl_studio_file(str(preset), include_channel_graph=True)
    assert "channel_graph" not in result
    assert result["channel_graph_skipped"] == "Not a project or channel-state container"
    project = flp(events())
    archive = tmp_path / "song.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("song.flp", project)
        z.writestr("private/audio.wav", b"PCM")
    output = inspect_fl_studio_file(str(archive), include_channel_graph=True)
    assert output["projects"][0]["channel_graph"]["channels"][1]["display_name"] == "142-Pizzstr"
    with zipfile.ZipFile(archive) as z:
        assert z.read("song.flp") == project
        assert z.read("private/audio.wav") == b"PCM"


def test_channel_graph_option_validated(tmp_path):
    """The optional MCP argument must be a boolean."""
    p = tmp_path / "my.flp"
    p.write_bytes(flp(events()))
    with pytest.raises(ValueError, match="include_channel_graph"):
        inspect_fl_studio_file(str(p), include_channel_graph=1)
    assert inspect_fl_studio_file(str(p))["valid_framing"] is True


def test_tool_forwards_channel_graph_option(tmp_path):
    """The existing MCP tool exposes the graph without requiring live FL Studio."""
    from fl_studio_mcp.tools.project_files import register_project_file_tools

    class Recorder:
        def __init__(self):
            self.tools = {}

        def tool(self):
            def register(fn):
                self.tools[fn.__name__] = fn
                return fn

            return register

    mcp = Recorder()
    register_project_file_tools(mcp)
    path = tmp_path / "my.flp"
    path.write_bytes(flp(events()))
    result = mcp.tools["fl_inspect_native_file"](str(path), include_channel_graph=True)
    assert result["channel_graph"]["channel_count"] == 3
    assert path.read_bytes() == flp(events())


def test_utf16_name_detected_when_version_metadata_missing():
    """A missing FL version must not silently turn 'Kick' into 'K'."""
    raw = bytes([64, 1, 0]) + text_event(203, "Kick")
    result = inspect_channel_graph(raw, fl_version=None)
    assert result["channels"][0]["display_name"] == "Kick"


def test_bounded_channel_inventory_reports_overflow_without_losing_first_ids():
    """Oversized but correctly framed channel lists remain bounded and explicit."""
    raw = b"".join(bytes([64]) + struct.pack("<H", i) for i in range(260))
    result = inspect_channel_graph(raw, fl_version="24.2")
    assert result["channel_count"] == 256
    assert result["channel_ids_over_limit"] == 4
    assert result["channels_truncated"] is True
    assert result["channels"][0]["channel_id"] == 0
