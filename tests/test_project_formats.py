"""Native container inspection must remain read-only and reject damaged framing."""
import struct
import zipfile

import pytest

from fl_studio_mcp.utils.project_formats import inspect_fl_studio_file


def flp(payload=b"\xc7\x07\x00"):
    return (
        b"FLhd" + struct.pack("<IHHH", 6, 0, 1, 96)
        + b"FLdt" + struct.pack("<I", len(payload)) + payload
    )


def test_flp_header(tmp_path):
    file = tmp_path / "song.flp"
    file.write_bytes(flp())
    result = inspect_fl_studio_file(str(file))
    assert result["kind"] == "flp"
    assert result["ppq"] == 96
    assert result["event_data_bytes"] == 3
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
