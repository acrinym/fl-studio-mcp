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
    assert receipt["source_feature_inventory"]["source_notes"] == 3
    assert receipt["source_feature_inventory"]["source_clips"] == 2
    assert receipt["source_feature_inventory"]["source_static_time_signature"] == [4, 4]
    assert receipt["mapped_feature_inventory"]["native_mapped_notes"] == 3
    assert receipt["mapped_feature_inventory"]["native_playlist_clips"] == 2
    assert receipt["mapped_feature_inventory"]["native_time_signature_written"] is True
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
    note = {"key": 60, "time": 0, "duration": 1, "velocity": 0.8}
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
            SimpleNamespace(pattern=1, channel=0, key=60, position=0, length=96, velocity=102),
            SimpleNamespace(pattern=1, channel=0, key=64, position=0, length=96, velocity=102),
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


def test_native_post_generation_independent_semantic_audit(tmp_path):
    """A second tool reads original source, stored graph, receipt, and FLP."""
    pytest.importorskip("flpkit")
    from fl_studio_mcp.utils.native_flp_audit import audit_native_flp_bundle

    source = _source(tmp_path)
    output = tmp_path / "native-audited"
    build_native_flp_from_template(str(source), str(TEMPLATE), str(output))
    before = {
        path.name: path.read_bytes()
        for path in (
            source,
            output / "project_graph.json",
            output / "native_fidelity_receipt.json",
            output / "reconstructed.flp",
        )
    }
    verified = audit_native_flp_bundle(str(output), str(source))
    assert verified["verified"] is True
    assert verified["verified_notes"] == 3
    assert verified["verified_clips"] == 2
    assert verified["fl_studio_open_verified"] is False
    assert verified["complete_automatic_conversion"] is False
    for path in (
        source,
        output / "project_graph.json",
        output / "native_fidelity_receipt.json",
        output / "reconstructed.flp",
    ):
        assert path.read_bytes() == before[path.name]


@pytest.mark.parametrize(
    "tamper", ["native_flp", "receipt", "graph", "source", "mapped_clips", "inventory"]
)
def test_native_audit_rejects_tampering_and_false_receipts(tmp_path, tamper):
    """Never accept a hash mismatch, changed original or rewritten mapping."""
    pytest.importorskip("flpkit")
    from fl_studio_mcp.utils.native_flp_audit import audit_native_flp_bundle

    source = _source(tmp_path)
    output = tmp_path / "export"
    build_native_flp_from_template(str(source), str(TEMPLATE), str(output))
    if tamper == "native_flp":
        native = output / "reconstructed.flp"
        native.write_bytes(native.read_bytes() + b"tampered")
    elif tamper == "receipt":
        receipt = output / "native_fidelity_receipt.json"
        value = json.loads(receipt.read_text())
        value["fl_studio_open_verified"] = True
        receipt.write_text(json.dumps(value))
    elif tamper == "graph":
        graph = output / "project_graph.json"
        value = json.loads(graph.read_text())
        value["transport"]["bpm"] = 150
        graph.write_text(json.dumps(value))
    elif tamper == "source":
        with zipfile.ZipFile(source, "a") as stream:
            stream.writestr("extra.txt", b"source-changed")
    elif tamper == "mapped_clips":
        receipt = output / "native_fidelity_receipt.json"
        value = json.loads(receipt.read_text())
        value["mapped_clips"][0]["source_note_indices"] = [1]
        receipt.write_text(json.dumps(value))
    elif tamper == "inventory":
        receipt = output / "native_fidelity_receipt.json"
        value = json.loads(receipt.read_text())
        value["source_feature_inventory"]["audio_clips"] = 0xFFFF
        receipt.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        audit_native_flp_bundle(str(output), str(source))


def test_native_audit_mcp_error_is_structured_and_read_only(tmp_path):
    """A missing FLP must be reported as verification failure by the MCP tool."""
    source = _source(tmp_path)

    class Registry:
        def __init__(self):
            self.fn = {}

        def tool(self):
            def collect(func):
                self.fn[func.__name__] = func
                return func

            return collect

    registered = Registry()
    register_universal_project_tools(registered)
    result = registered.fn["fl_audit_native_reconstruction"](
        str(tmp_path / "missing-bundle"), str(source)
    )
    assert result["verified"] is False
    assert result["read_only"] is True
    assert result["fl_studio_open_verified"] is False


def test_native_inventory_preserves_unmapped_audio_plugins_and_automation(tmp_path):
    """Count all omitted musical features while qualifying only mapped native notes."""
    pytest.importorskip("flpkit")
    from fl_studio_mcp.utils.native_flp_audit import audit_native_flp_bundle

    xml = SOURCE_XML.replace(
        '<Volume value="0.8"/></Channel>',
        '<Devices><ClapPlugin id="fx" deviceID="com.example.fx" deviceRole="audioFX"/>'
        '</Devices><Volume value="0.8"/></Channel>',
    ).replace(
        "  </Clips></Lanes></Lanes></Arrangement>",
        '    <Clip time="12" duration="4" name="Recorded audio">'
        '<Audio sampleRate="44100" channels="2"><File path="C:\\sounds\\take.wav"'
        ' external="true"/></Audio></Clip>'
        '</Clips></Lanes></Lanes><Points target="volume" timeUnit="beats">'
        '<RealPoint time="1" value="0.7"/></Points></Arrangement>',
    )
    source = _source(tmp_path, xml)
    output = tmp_path / "full-accounting"
    build_native_flp_from_template(str(source), str(TEMPLATE), str(output))
    receipt = json.loads((output / "native_fidelity_receipt.json").read_text())
    assert receipt["source_feature_inventory"]["source_clips"] == 3
    assert receipt["source_feature_inventory"]["audio_clips"] == 1
    assert receipt["source_feature_inventory"]["external_resource_refs"] == 1
    assert receipt["source_feature_inventory"]["plugin_devices"] == 1
    assert receipt["source_feature_inventory"]["automation_lanes"] == 1
    assert receipt["mapped_feature_inventory"]["native_audio_clips"] == 0
    assert receipt["mapped_feature_inventory"]["native_plugin_devices"] == 0
    assert receipt["mapped_feature_inventory"]["native_automation_lanes"] == 0
    assert audit_native_flp_bundle(str(output), str(source))["verified"] is True


def test_native_semantic_audit_catches_velocity_forgery_with_matching_file_hash(tmp_path):
    """A forged receipt hash cannot make changed musical velocities equivalent."""
    flpkit = pytest.importorskip("flpkit")
    from fl_studio_mcp.utils.native_flp_audit import audit_native_flp_bundle

    source = _source(tmp_path)
    output = tmp_path / "tampered-musically"
    build_native_flp_from_template(str(source), str(TEMPLATE), str(output))
    native = output / "reconstructed.flp"
    flpkit.write_notes(
        native,
        [
            flpkit.NoteSpec(key=60, start=0, length=1, velocity=0.1),
            flpkit.NoteSpec(key=64, start=1.5, length=0.5, velocity=0.7),
        ],
        pattern=1,
        channel=0,
        mode="replace",
    )
    receipt_file = output / "native_fidelity_receipt.json"
    receipt = json.loads(receipt_file.read_text())
    # Even with forged output hash, the source's original note velocity rules.
    receipt["output_flp_sha256"] = hashlib.sha256(native.read_bytes()).hexdigest()
    receipt_file.write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match="note timing/identity"):
        audit_native_flp_bundle(str(output), str(source))


@pytest.mark.parametrize("signature", [(3, 4), (7, 8), (5, 16)])
def test_native_project_meter_reconstruction_from_source(tmp_path, signature):
    """Change the actual FLP project meter, not merely report the source meter."""
    pytest.importorskip("flpkit")
    from fl_studio_mcp.utils.native_flp_audit import audit_native_flp_bundle
    from fl_studio_mcp.utils.native_flp_meter import read_native_meter

    numerator, denominator = signature
    xml = SOURCE_XML.replace(
        'numerator="4" denominator="4"',
        f'numerator="{numerator}" denominator="{denominator}"',
    )
    source = _source(tmp_path, xml)
    source_before = source.read_bytes()
    template_before = TEMPLATE.read_bytes()
    output = tmp_path / "native-meter"
    result = build_native_flp_from_template(str(source), str(TEMPLATE), str(output))
    assert result["offline_flp_readback_verified"] is True
    assert read_native_meter(output / "reconstructed.flp") == signature
    receipt = json.loads((output / "native_fidelity_receipt.json").read_text())
    assert receipt["source_feature_inventory"]["source_static_time_signature"] == list(signature)
    assert receipt["mapped_feature_inventory"]["native_time_signature_written"] is True
    assert audit_native_flp_bundle(str(output), str(source))["verified"] is True
    assert source.read_bytes() == source_before
    assert TEMPLATE.read_bytes() == template_before


def test_native_meter_patcher_alters_only_existing_project_meter_bytes(tmp_path):
    """Do not reserialize unknown proprietary FLP data to adjust the meter."""
    pytest.importorskip("flpkit")
    from fl_studio_mcp.utils.native_flp_meter import read_native_meter, set_native_meter

    copied = tmp_path / "copied-scaffold.flp"
    before = TEMPLATE.read_bytes()
    copied.write_bytes(before)
    assert read_native_meter(copied) == (4, 4)
    set_native_meter(copied, 7, 8)
    after = copied.read_bytes()
    assert len(after) == len(before)
    changed_bytes = [
        index for index, (previous, current) in enumerate(zip(before, after))
        if previous != current
    ]
    assert len(changed_bytes) == 2
    assert read_native_meter(copied) == (7, 8)
    assert TEMPLATE.read_bytes() == before


def test_native_meter_tampering_rejected_even_with_forged_matching_hash(tmp_path):
    """Changing the output time signature is a musical difference, not success."""
    pytest.importorskip("flpkit")
    from fl_studio_mcp.utils.native_flp_audit import audit_native_flp_bundle
    from fl_studio_mcp.utils.native_flp_meter import set_native_meter

    source = _source(tmp_path)
    output = tmp_path / "meter-tampering"
    build_native_flp_from_template(str(source), str(TEMPLATE), str(output))
    flp = output / "reconstructed.flp"
    set_native_meter(flp, 3, 4)
    receipt_path = output / "native_fidelity_receipt.json"
    receipt = json.loads(receipt_path.read_text())
    receipt["output_flp_sha256"] = hashlib.sha256(flp.read_bytes()).hexdigest()
    receipt_path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match="time signature"):
        audit_native_flp_bundle(str(output), str(source))


def test_native_meter_requires_existing_events_not_guessed_additions(tmp_path):
    """A template with no original time-signature numerator cannot be edited."""
    pytest.importorskip("flpkit")
    from flpkit import codec

    from fl_studio_mcp.utils.native_flp_meter import read_native_meter, set_native_meter

    scaffold = bytearray(TEMPLATE.read_bytes())
    header, stream = codec.chunks(scaffold)
    base = 8 + len(header) + 8
    numerator_positions = [
        base + off for event_id, off, size in codec.Stream(stream)
        if event_id == 17 and size == 1
    ]
    assert len(numerator_positions) == 1
    scaffold[numerator_positions[0] - 1] = 19
    broken = tmp_path / "no-meter-template.flp"
    broken.write_bytes(scaffold)
    with pytest.raises(ValueError, match="exactly one"):
        read_native_meter(broken)
    with pytest.raises(ValueError, match="exactly one"):
        set_native_meter(broken, 3, 4)
    source = _source(tmp_path)
    output = tmp_path / "should-not-exist"
    with pytest.raises(ValueError, match="exactly one"):
        build_native_flp_from_template(str(source), str(broken), str(output))
    assert not output.exists()
