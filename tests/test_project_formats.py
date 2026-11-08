"""Native container inspection must remain read-only and reject damaged framing."""

import struct
import zipfile

import pytest

from fl_studio_mcp.utils.project_formats import inspect_fl_studio_file


def flp(payload=b"\xc7\x05" + b"24.1\x00"):
    return (
        b"FLhd"
        + struct.pack("<IHHH", 6, 0, 1, 96)
        + b"FLdt"
        + struct.pack("<I", len(payload))
        + payload
    )


def test_flp_header(tmp_path):
    file = tmp_path / "song.flp"
    file.write_bytes(flp())
    result = inspect_fl_studio_file(str(file))
    assert result["kind"] == "flp"
    assert result["ppq"] == 96
    assert result["event_data_bytes"] == 7
    assert result["event_summary"]["fl_version"] == "24.1"
    assert result["format_name"] == "project"
    assert file.read_bytes() == flp()


def test_fst_header(tmp_path):
    file = tmp_path / "plugin.fst"
    file.write_bytes(flp(b"\x01\x02"))
    assert inspect_fl_studio_file(str(file))["kind"] == "fst"


def test_reject_null_and_truncated(tmp_path):
    file = tmp_path / "broken.flp"
    for contents in (b"\0" * 100, flp()[:-1], b"FLhd" + b"\0" * 15):
        file.write_bytes(contents)
        with pytest.raises(ValueError):
            inspect_fl_studio_file(str(file))


def test_zipped_project_no_extraction(tmp_path):
    file = tmp_path / "song.zip"
    with zipfile.ZipFile(file, "w") as archive:
        archive.writestr("untitled.flp", flp())
        archive.writestr("../outside.wav", b"data")
    result = inspect_fl_studio_file(str(file))
    assert result["kind"] == "zipped_project"
    assert result["projects"][0]["member"] == "untitled.flp"
    assert not (tmp_path.parent / "outside.wav").exists()


def test_corrupt_zip_project_does_not_hide_valid_project(tmp_path):
    file = tmp_path / "mixed.zip"
    with zipfile.ZipFile(file, "w") as archive:
        archive.writestr("good.flp", flp())
        archive.writestr("broken.flp", b"FLhd" + b"\x00" * 20)
        archive.writestr("samples/kick.wav", b"audio")
    result = inspect_fl_studio_file(str(file))
    assert result["member_count"] == 3
    assert result["projects"][0]["member"] == "good.flp"
    assert result["projects"][0]["valid_framing"] is True
    assert result["projects"][1]["member"] == "broken.flp"
    assert "error" in result["projects"][1]
    assert not (tmp_path / "samples").exists()


def test_zip_inventory_truncation(tmp_path):
    file = tmp_path / "large-index.zip"
    with zipfile.ZipFile(file, "w") as archive:
        archive.writestr("song.flp", flp())
        for index in range(103):
            archive.writestr(f"samples/{index}.wav", b"")
    result = inspect_fl_studio_file(str(file))
    assert result["member_count"] == 104
    assert len(result["members"]) == 100
    assert result["members_truncated"] is True


def test_mcp_tool_wraps_inspection_without_mutation(tmp_path):
    from fl_studio_mcp.tools.project_files import register_project_file_tools

    class RecordingMCP:
        tools = {}

        def tool(self):
            def register(fn):
                self.tools[fn.__name__] = fn
                return fn

            return register

    mcp = RecordingMCP()
    register_project_file_tools(mcp)
    tool = mcp.tools["fl_inspect_native_file"]
    file = tmp_path / "test.flp"
    content = flp()
    file.write_bytes(content)
    result = tool(str(file))
    assert result["valid_framing"] is True
    assert file.read_bytes() == content
    error = tool(str(tmp_path / "missing.flp"))
    assert error["read_only"] is True
    assert "error" in error
