"""Synthetic bounded mixer inspection tests; no real project is modified."""

from __future__ import annotations

import struct
import zipfile

import pytest

from fl_studio_mcp.utils.mixer_graph import inspect_mixer_graph
from fl_studio_mcp.utils.project_formats import inspect_fl_studio_file


def var_event(eid: int, payload: bytes) -> bytes:
    n = len(payload)
    encoded = bytearray()
    while n >= 128:
        encoded.append((n & 127) | 128)
        n >>= 7
    encoded.append(n)
    return bytes([eid]) + bytes(encoded) + payload


def string(eid: int, name: str) -> bytes:
    return var_event(eid, (name + "\x00").encode("utf-16-le"))


def flag_event(flag: int = 12) -> bytes:
    return var_event(236, struct.pack("<III", 0x80000000, flag, 0))


def parameter(eid: int, record: int, index: int, value: int) -> bytes:
    return struct.pack("<4sBBHi", b"\0" * 4, eid, 0, (record << 6) | index, value)


def fixture() -> bytes:
    raw_mask = bytearray(127)
    raw_mask[0] = raw_mask[2] = 1
    return b"".join(
        [
            var_event(199, b"24.2\x00"),
            flag_event(12),  # first mixer record
            string(204, "Master"),
            bytes([98]) + struct.pack("<H", 0),
            var_event(235, bytes(127)),
            bytes([154]) + struct.pack("<i", -1),
            bytes([147]) + struct.pack("<i", -1),
            flag_event(76),
            string(204, "Pizz Bus"),
            bytes([98]) + struct.pack("<H", 0),
            string(201, "Fruity Wrapper"),
            string(203, "Vintage Compressor"),
            var_event(212, b"wrapper"),
            var_event(213, b"PRIVATE-NATIVE-STATE"),
            bytes([98]) + struct.pack("<H", 1),  # unoccupied slot
            var_event(235, bytes(raw_mask)),
            var_event(
                225,
                b"".join(
                    [
                        parameter(192, 0, 0, 12345),
                        parameter(193, 0, 0, -100),
                        parameter(194, 1, 0, 20),
                        parameter(66, 1, 0, 999),
                        parameter(0, 1, 0, 1),
                        parameter(1, 1, 0, 4400),
                    ]
                ),
            ),
        ]
    )


def flp(stream: bytes, kind: int = 0) -> bytes:
    return (
        b"FLhd"
        + struct.pack("<IHHH", 6, kind, 2, 96)
        + b"FLdt"
        + struct.pack("<I", len(stream))
        + stream
    )


def test_mixer_slots_parameters_and_masks_are_bounded_and_private():
    graph = inspect_mixer_graph(fixture(), fl_version="24.2")
    assert graph["mixer_record_count"] == 2
    assert graph["mixer_records_truncated"] is False
    first, second = graph["mixer_records"]
    assert first["display_name"] == "Master"
    assert first["flags_raw"] == 12
    assert first["enabled"] is True
    assert first["effects_enabled"] is True
    assert first["volume_raw"] == 12345
    assert first["pan_raw"] == -100
    assert first["input_raw"] == -1
    assert first["output_raw"] == -1
    assert first["occupied_fx_slots"] == []
    assert second["display_name"] == "Pizz Bus"
    assert second["routing_mask_set_indices"] == [0, 2]
    assert second["send_parameter_target_indices"] == [2]
    assert second["stereo_separation_raw"] == 20
    assert second["declared_fx_slot_count"] == 2
    assert len(second["occupied_fx_slots"]) == 1
    fx = second["occupied_fx_slots"][0]
    assert fx == {
        "slot_index": 0,
        "internal_plugin_name": "Fruity Wrapper",
        "display_name": "Vintage Compressor",
        "has_plugin_wrapper": True,
        "opaque_plugin_data_bytes": 20,
        "enabled_raw": 1,
        "mix_raw": 4400,
    }
    assert "PRIVATE" not in str(graph)
    assert "NATIVE-STATE" not in str(graph)


def test_limit_counts_and_invalid_parameter_layout_are_reported():
    stream = fixture() + var_event(225, b"broken")
    graph = inspect_mixer_graph(stream, fl_version="24.2", max_mixer_inserts=1)
    assert graph["mixer_record_count"] == 2
    assert graph["mixer_records_returned"] == 1
    assert graph["mixer_records_truncated"] is True
    assert graph["unrecognized_mixer_parameter_records"] == 1
    with pytest.raises(ValueError, match="max_mixer_inserts"):
        inspect_mixer_graph(fixture(), max_mixer_inserts=129)


def test_mixer_event_limit_and_unknown_flag_structure():
    stream = var_event(236, b"x") + flag_event() * 257
    graph = inspect_mixer_graph(stream)
    assert graph["mixer_record_count"] == 256
    assert graph["mixer_records_over_limit"] == 2
    assert graph["unknown_mixer_flag_blocks"] == 1
    assert graph["mixer_records_truncated"] is True


def test_mixer_option_flp_zip_fst_and_tool(tmp_path):
    project = tmp_path / "session.flp"
    content = flp(fixture())
    project.write_bytes(content)
    result = inspect_fl_studio_file(str(project), include_mixer_graph=True)
    assert result["mixer_graph"]["mixer_record_count"] == 2
    assert project.read_bytes() == content

    archive = tmp_path / "session.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("session.flp", content)
        z.writestr("samples/private.wav", b"sample")
    output = inspect_fl_studio_file(str(archive), include_mixer_graph=True)
    assert output["projects"][0]["mixer_graph"]["mixer_record_count"] == 2
    with zipfile.ZipFile(archive) as z:
        assert z.read("samples/private.wav") == b"sample"

    preset = tmp_path / "bus.fst"
    preset.write_bytes(flp(fixture(), kind=64))
    assert (
        inspect_fl_studio_file(str(preset), include_mixer_graph=True)["mixer_graph"][
            "mixer_record_count"
        ]
        == 2
    )

    class MCP:
        tools = {}

        def tool(self):
            def register(fn):
                self.tools[fn.__name__] = fn
                return fn

            return register

    from fl_studio_mcp.tools.project_files import register_project_file_tools

    mcp = MCP()
    register_project_file_tools(mcp)
    via = mcp.tools["fl_inspect_native_file"](str(project), include_mixer_graph=True)
    assert via["mixer_graph"]["occupied_effect_slots_in_all_records"] == 1
    assert project.read_bytes() == content


def test_reject_non_mixer_format_and_invalid_args(tmp_path):
    preset = tmp_path / "instrument.fst"
    preset.write_bytes(flp(fixture(), kind=48))
    result = inspect_fl_studio_file(str(preset), include_mixer_graph=True)
    assert result["mixer_graph_skipped"] == "Not a project or mixer insert-state container"
    assert "mixer_graph" not in result
    for bad in (-1, 129, "4", True):
        with pytest.raises(ValueError, match="max_mixer_inserts"):
            inspect_fl_studio_file(str(preset), max_mixer_inserts=bad)
    with pytest.raises(ValueError, match="include_mixer_graph"):
        inspect_fl_studio_file(str(preset), include_mixer_graph="yes")


def test_fx_context_ends_at_routing_boundary():
    """Plugin-like events after routing cannot be assigned to a prior FX slot."""
    stream = b"".join(
        [
            var_event(199, b"24.2\x00"),
            flag_event(12),
            bytes([98]) + struct.pack("<H", 0),
            var_event(235, b"\x00" * 127),
            string(201, "NOT_AN_EFFECT"),
        ]
    )
    result = inspect_mixer_graph(stream, fl_version="24.2")
    assert result["mixer_records"][0]["declared_fx_slot_count"] == 1
    assert result["mixer_records"][0]["occupied_fx_slots"] == []
    assert "NOT_AN_EFFECT" not in str(result)


def test_named_and_occupied_records_beyond_default_output_are_discoverable():
    """Consumers can identify which raw mixer records need a larger output cap."""
    stream = (
        b"".join(flag_event(76) for _ in range(39))
        + flag_event(76)
        + string(204, "Far Bus")
        + bytes([98])
        + struct.pack("<H", 0)
        + string(201, "Fruity Wrapper")
    )
    output = inspect_mixer_graph(stream, fl_version="24.2")
    assert output["mixer_record_count"] == 40
    assert output["mixer_records_returned"] == 32
    assert output["mixer_records_with_effects"] == [39]
    assert output["mixer_records_with_names"] == [39]
    expanded = inspect_mixer_graph(stream, fl_version="24.2", max_mixer_inserts=40)
    assert (
        expanded["mixer_records"][39]["occupied_fx_slots"][0]["internal_plugin_name"]
        == "Fruity Wrapper"
    )
