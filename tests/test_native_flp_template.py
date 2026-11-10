"""Offline native FLP reconstruction through MIT flpkit and a blank scaffold.

The committed binary test scaffold combines an FL Studio 25-authored empty
project with the independent MIT flpkit playlist test record. The fixture
is synthetic and has NOT been app-open verified.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from fl_studio_mcp.tools.universal_project import register_universal_project_tools
from fl_studio_mcp.utils.native_flp_template import build_native_flp_from_template

TEMPLATE = Path(__file__).parent / "fixtures" / "native_flp_scaffold.flp"
TEMPLATE_SHA = "9449554bbd72861b06aed33731372b61ccf76b9b84c467b4f9d9e5764c1cca98"

SOURCE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Project version="1.0">
  <Application name="Onyx Fixture DAW" version="1"/>
  <Transport>
    <Tempo unit="bpm" value="132"/>
    <TimeSignature numerator="4" denominator="4"/>
  </Transport>
  <Structure>
    <Track id="melody" name="Melody" contentType="notes">
      <Channel id="melody_channel" role="regular"><Volume value="0.8"/></Channel>
    </Track>
  </Structure>
  <Arrangement><Lanes timeUnit="beats"><Lanes track="melody"><Clips>
    <Clip time="0" duration="4" name="Verse"><Notes>
      <Note time="0" duration="1" key="60" vel="0.8" channel="0"/>
      <Note time="1.5" duration="0.5" key="64" vel="0.7" channel="0"/>
    </Notes></Clip>
    <Clip time="8" duration="4" name="Chorus"><Notes>
      <Note time="0.5" duration="1" key="67" vel="0.9" channel="0"/>
    </Notes></Clip>
  </Clips></Lanes></Lanes></Arrangement>
</Project>
"""


def _source(tmp_path, xml=SOURCE_XML):
    """Make an isolated source .dawproject without modifying user sessions."""
    path = tmp_path / "source.dawproject"
    with zipfile.ZipFile(path, "w") as stream:
        stream.writestr("project.xml", xml)
    return path


def test_offline_native_flp_notes_playlist_and_receipt(tmp_path):
    """Write native FLP bytes and reread tempo, notes and playlist from the output."""
    flpkit = pytest.importorskip("flpkit")
    assert hashlib.sha256(TEMPLATE.read_bytes()).hexdigest() == TEMPLATE_SHA
    source = _source(tmp_path)
    source_original = source.read_bytes()
    template_original = TEMPLATE.read_bytes()
    dest = tmp_path / "native-export"
    result = build_native_flp_from_template(str(source), str(TEMPLATE), str(dest))
    assert result["native_flp_created"] is True
    assert result["offline_flp_readback_verified"] is True
    assert result["fl_studio_open_verified"] is False
    assert result["complete_automatic_conversion"] is False
    assert result["source_notes"] == result["exported_notes"] == 3
    assert source.read_bytes() == source_original
    assert TEMPLATE.read_bytes() == template_original
    native = flpkit.read(dest / "reconstructed.flp")
    assert native.fl_version.startswith("25.")
    assert native.ppq == 96
    assert abs(native.tempo - 132) < 0.01
    assert {(n.key, n.position, n.length, n.pattern, n.channel) for n in native.notes} == {
        (60, 0, 96, 1, 0),
        (64, 144, 48, 1, 0),
        (67, 48, 96, 2, 0),
    }
    assert {(p.position, p.length, p.track, p.pattern) for p in native.playlist} == {
        (0, 384, 1, 1),
        (768, 384, 1, 2),
    }
    receipt = json.loads((dest / "native_fidelity_receipt.json").read_text())
    assert (
        receipt["output_flp_sha256"]
        == hashlib.sha256((dest / "reconstructed.flp").read_bytes()).hexdigest()
    )
    assert receipt["template_sha256"] == TEMPLATE_SHA
    assert receipt["fl_studio_resave_verified"] is False
    assert receipt["native_flp_created"] is True
    assert receipt["note_status"] == "converted with declared mapping (offline only)"
    assert len(receipt["mapped_clips"]) == 2
    assert (
        "Device/plugin identities, presets and vendor-state portability"
        in receipt["unverified_features"]
    )


def test_native_template_does_not_overwrite_any_path(tmp_path):
    """Refuse destinations that already exist without changing either input."""
    pytest.importorskip("flpkit")
    source = _source(tmp_path)
    before = source.read_bytes()
    dest = tmp_path / "exists"
    dest.mkdir()
    marker = dest / "original"
    marker.write_text("safe")
    with pytest.raises(FileExistsError, match="overwrite"):
        build_native_flp_from_template(str(source), str(TEMPLATE), str(dest))
    assert marker.read_text() == "safe"
    assert source.read_bytes() == before


def test_native_partial_clip_reconstruction_advertises_loss(tmp_path):
    """Nested notes are retained in graph, never emitted at incorrect FLP ticks."""
    pytest.importorskip("flpkit")
    source = _source(
        tmp_path,
        SOURCE_XML.replace(
            '<Note time="0.5" duration="1" key="67" vel="0.9" channel="0"/>',
            '<Clips><Clip time="1" duration="2"><Notes>'
            '<Note time="0.5" duration="1" key="67" vel="0.9" channel="0"/>'
            "</Notes></Clip></Clips>",
        ),
    )
    dest = tmp_path / "partial"
    result = build_native_flp_from_template(str(source), str(TEMPLATE), str(dest))
    assert result["source_notes"] == 3
    assert result["exported_notes"] == 2
    receipt = json.loads((dest / "native_fidelity_receipt.json").read_text())
    assert receipt["note_status"] == "approximated / lossy"
    assert receipt["complete_automatic_conversion"] is False
    assert any("nested_clip_timing_not_resolved" in x["reasons"] for x in receipt["skipped"])


def test_native_template_requires_existing_playlist_scaffold(tmp_path):
    """Reject a real FLP template whose playlist has no pattern clip record."""
    pytest.importorskip("flpkit")
    source = _source(tmp_path)
    template_bytes = TEMPLATE.read_bytes()
    # A deliberately invalid template rejects cleanly without an output.
    invalid = tmp_path / "not-flp.flp"
    invalid.write_bytes(template_bytes[:18])
    dest = tmp_path / "not-generated"
    with pytest.raises((ValueError, RuntimeError, EOFError, IndexError)):
        build_native_flp_from_template(str(source), str(invalid), str(dest))
    assert not dest.exists()


def test_native_mcp_surfaces_missing_scaffold_error(tmp_path):
    """An untrusted missing template gets a structured MCP error, never a save."""
    source = _source(tmp_path)

    class Registry:
        """Simple registrar sufficient for public MCP function inspection."""

        def __init__(self):
            self.fn = {}

        def tool(self):
            """Store wrapped tool callbacks without controlling a real DAW."""

            def collect(func):
                """Return the original function while registering it."""
                self.fn[func.__name__] = func
                return func

            return collect

    registry = Registry()
    register_universal_project_tools(registry)
    result = registry.fn["fl_build_native_flp_from_template"](
        str(source), str(tmp_path / "missing.flp"), str(tmp_path / "output")
    )
    assert "error" in result
    assert result["native_flp_created"] is False
    assert result["source_modified"] is False
    assert not (tmp_path / "output").exists()


def test_native_readback_rejects_duplicate_count_mismatch():
    """Readback must compare multisets, not just distinct keys and positions."""
    from types import SimpleNamespace

    from fl_studio_mcp.utils.native_flp_template import _readback_matches

    clip = {"start": 0, "duration": 2}
    note = {"key": 60, "time": 0, "duration": 1}
    assignment = {
        "clip": clip,
        "notes": [note, note],
        "pattern": 1,
        "channel": 0,
        "playlist_track": 1,
    }
    project = SimpleNamespace(
        playlist=[
            SimpleNamespace(
                position=0,
                length=192,
                track=1,
                pattern=1,
                arrangement=0,
            )
        ],
        notes=[
            SimpleNamespace(pattern=1, channel=0, key=60, position=0, length=96),
            SimpleNamespace(pattern=1, channel=0, key=64, position=0, length=96),
        ],
    )
    with pytest.raises(ValueError, match="note timing/identity"):
        _readback_matches(project, [assignment], 96)


def test_native_note_release_gets_lossy_fidelity_receipt(tmp_path):
    """Dropped release/expressions must never retain an exact note-fidelity claim."""
    pytest.importorskip("flpkit")
    xml = SOURCE_XML.replace('key="60" vel="0.8"', 'key="60" vel="0.8" rel="0.7"')
    source = _source(tmp_path, xml)
    dest = tmp_path / "release-output"
    result = build_native_flp_from_template(str(source), str(TEMPLATE), str(dest))
    assert result["source_notes"] == result["exported_notes"] == 3
    receipt = json.loads((dest / "native_fidelity_receipt.json").read_text())
    assert receipt["source_notes_with_expression_or_release"] == 1
    assert receipt["note_status"] == "approximated / lossy"
    assert receipt["fl_studio_open_verified"] is False
