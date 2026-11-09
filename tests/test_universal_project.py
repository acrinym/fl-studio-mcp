"""Universal DAWproject musical graph -> FL Studio reconstruction E2E tests.

Synthetic but specification-shaped DAWproject ZIPs; all writes confined to tmp_path.
No installed FL Studio or original user project is modified.
"""

from __future__ import annotations

import hashlib
import json
import zipfile

import mido
import pytest

from fl_studio_mcp.tools.universal_project import register_universal_project_tools
from fl_studio_mcp.utils.reconstruction_bundle import build_dawproject_bundle
from fl_studio_mcp.utils.universal_project import read_dawproject

PROJECT_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Project version="1.0">
  <Application name="Bitwig Studio" version="5.0"/>
  <Transport>
    <Tempo unit="bpm" value="149.0" id="tempo"/>
    <TimeSignature numerator="4" denominator="4" id="sig"/>
  </Transport>
  <Structure>
    <Track contentType="notes" id="bass" name="Bass">
      <Channel id="ch_bass" role="regular" destination="master_ch">
        <Devices>
          <ClapPlugin id="inst" deviceID="org.surge-synth-team.surge-xt"
                      deviceName="Surge XT" deviceRole="instrument" loaded="true">
            <State path="plugins/patch.clap-preset"/>
          </ClapPlugin>
        </Devices>
        <Pan value="0.50"/><Volume value="0.65914"/><Mute value="false"/>
      </Channel>
    </Track>
    <Track contentType="audio" id="drums" name="Drumloop">
      <Channel id="ch_drums" role="regular" destination="master_ch">
        <Volume value="0.177125"/>
      </Channel>
    </Track>
    <Track id="master" name="Master">
      <Channel id="master_ch" role="master"><Volume value="1.0"/></Channel>
    </Track>
  </Structure>
  <Arrangement id="arr">
    <Lanes timeUnit="beats">
      <Lanes track="bass">
        <Clips>
          <Clip time="0" duration="8" playStart="0" name="Bass Intro">
            <Notes>
              <Note time="0" duration="0.25" channel="0" key="65" vel="0.8" rel="0.7"/>
              <Note time="4" duration="0.5" channel="0" key="53" vel="0.5" rel="0.7"/>
            </Notes>
          </Clip>
          <Clip time="8" duration="4" playStart="0" name="Bass Outro">
            <Notes>
              <Note time="1" duration="0.5" channel="0" key="60" vel="0.6" rel="0.7"/>
            </Notes>
          </Clip>
        </Clips>
      </Lanes>
      <Lanes track="drums">
        <Clips><Clip time="0" duration="8" name="Drum Loop">
          <Clips><Clip time="0" duration="8">
            <Warps timeUnit="beats" contentTimeUnit="seconds">
              <Audio sampleRate="48000" channels="2" duration="2.823">
                <File path="audio/drum-loop.wav"/>
              </Audio>
              <Warp time="0" contentTime="0"/>
              <Warp time="8" contentTime="2.823"/>
            </Warps>
          </Clip></Clips>
        </Clip></Clips>
      </Lanes>
    </Lanes>
  </Arrangement>
  <Scenes/>
</Project>
"""


def source_zip(
    path, *, xml=PROJECT_XML, sample=b"real-audio-bytes", state=b"opaque-plugin-preset", extras=None
):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("project.xml", xml)
        z.writestr("metadata.xml", "<MetaData/>")
        z.writestr("audio/drum-loop.wav", sample)
        z.writestr("plugins/patch.clap-preset", state)
        for key, value in (extras or {}).items():
            z.writestr(key, value)
    return path


def test_dawproject_graph_and_bundle_true_end_to_end(tmp_path):
    source = source_zip(tmp_path / "session.dawproject")
    original = source.read_bytes()
    graph = read_dawproject(str(source))
    assert graph["schema"] == "universal-musical-project/1"
    assert graph["source"]["sha256"] == hashlib.sha256(original).hexdigest()
    assert graph["transport"]["bpm"] == 149
    assert graph["transport"]["signature"] == [4, 4]
    assert [t["name"] for t in graph["tracks"]] == ["Bass", "Drumloop", "Master"]
    assert graph["tracks"][0]["destination"] == "master_ch"
    assert graph["tracks"][0]["devices"][0]["device_id"] == "org.surge-synth-team.surge-xt"
    assert [c["start"] for c in graph["clips"]] == [0, 8, 0]
    assert [len(c["notes"]) for c in graph["clips"]] == [2, 1, 0]
    assert len(graph["resources"]) == 2

    output = tmp_path / "safe-new-output"
    result = build_dawproject_bundle(str(source), str(output))
    assert result["source_notes"] == result["exported_notes"] == 3
    assert result["native_flp_created"] is False
    assert output.is_dir()
    assert source.read_bytes() == original
    manifest = json.loads((output / "project_graph.json").read_text(encoding="utf-8"))
    assert manifest["clips"] == graph["clips"]
    assert (output / "resources" / "audio" / "drum-loop.wav").read_bytes() == b"real-audio-bytes"
    assert (
        output / "resources" / "plugins" / "patch.clap-preset"
    ).read_bytes() == b"opaque-plugin-preset"

    midi = mido.MidiFile(output / "midi" / "track-0000.mid")
    assert midi.ticks_per_beat == 960
    assert next(m for m in midi.tracks[0] if m.type == "set_tempo").tempo == round(60_000_000 / 149)
    active = [m for m in midi.tracks[0] if m.type == "note_on"]
    assert [n.note for n in active] == [65, 53, 60]
    # Absolute ticks: source clip instance at beat 8 + relative note at beat 1.
    pos = 0
    on_times = []
    for msg in midi.tracks[0]:
        pos += msg.time
        if msg.type == "note_on":
            on_times.append(pos)
    assert on_times == [0, 4 * 960, 9 * 960]

    receipt = json.loads((output / "fidelity_receipt.json").read_text(encoding="utf-8"))
    assert receipt["features"]["notes"]["status"] == "converted with declared mapping"
    assert receipt["features"]["audio_clips"]["status"] == "unknown/unverified in FL Studio"
    assert receipt["features"]["devices"]["count"] == 1
    assert receipt["features"]["routing"]["routes"] == 2
    assert receipt["fl_studio_open_verified"] is False
    assert receipt["complete_automatic_conversion"] is False
    assert receipt["native_flp_created"] is False


def test_existing_output_is_never_overwritten(tmp_path):
    source = source_zip(tmp_path / "session.dawproject")
    output = tmp_path / "existing"
    output.mkdir()
    (output / "keep.txt").write_text("USER CONTENT", encoding="utf-8")
    with pytest.raises(FileExistsError):
        build_dawproject_bundle(str(source), str(output))
    assert (output / "keep.txt").read_text(encoding="utf-8") == "USER CONTENT"


@pytest.mark.parametrize("member", ["../escape.wav", "/absolute.wav", "C:/win.wav"])
def test_zip_traversal_and_unsafe_paths_rejected(tmp_path, member):
    source = source_zip(tmp_path / "evil.dawproject", extras={member: b"bad"})
    with pytest.raises(ValueError, match="Unsafe"):
        read_dawproject(str(source))


def test_case_colliding_resources_rejected(tmp_path):
    source = source_zip(
        tmp_path / "duplicate.dawproject", extras={"AUDIO/DRUM-LOOP.WAV": b"different"}
    )
    with pytest.raises(ValueError, match="duplicate or case-colliding"):
        read_dawproject(str(source))


def test_rejects_dtd_and_unknown_schema_version(tmp_path):
    dtd = '<!DOCTYPE Project [<!ENTITY hidden SYSTEM "file:///C:/secret.txt">]>'
    source = source_zip(tmp_path / "evil.dawproject", xml=dtd + PROJECT_XML)
    with pytest.raises(ValueError, match="DTD"):
        read_dawproject(str(source))
    source_zip(
        source, xml=PROJECT_XML.replace('<Project version="1.0">', '<Project version="2.0">', 1)
    )
    with pytest.raises(ValueError, match="Unsupported"):
        read_dawproject(str(source))


def test_missing_media_is_reported_not_manufactured(tmp_path):
    source = source_zip(
        tmp_path / "absent.dawproject",
        xml=PROJECT_XML.replace("audio/drum-loop.wav", "missing/asset.wav"),
    )
    output = tmp_path / "bundle"
    build_dawproject_bundle(str(source), str(output))
    receipt = json.loads((output / "fidelity_receipt.json").read_text(encoding="utf-8"))
    assert any(
        item["status"] == "unavailable/missing"
        for item in receipt["features"]["embedded_resources"]["files"]
    )
    assert not (output / "resources" / "missing" / "asset.wav").exists()


def test_loop_and_seconds_timing_not_masqueraded_as_exact(tmp_path):
    xml = PROJECT_XML.replace(
        'playStart="0" name="Bass Intro"',
        'playStart="0" loopStart="0" loopEnd="4" name="Bass Intro"',
    )
    source = source_zip(tmp_path / "odd.dawproject", xml=xml)
    output = tmp_path / "odd-bundle"
    result = build_dawproject_bundle(str(source), str(output))
    assert result["source_notes"] == 3
    assert result["exported_notes"] == 1
    receipt = json.loads((output / "fidelity_receipt.json").read_text(encoding="utf-8"))
    assert receipt["features"]["notes"]["status"] == "approximated / lossy"
    assert receipt["features"]["notes"]["skipped"][0]["reason"] == "loop_expansion_not_verified"


def test_invalid_note_and_duplicate_track_ids_fail_closed(tmp_path):
    source = source_zip(
        tmp_path / "malformed.dawproject", xml=PROJECT_XML.replace('key="65"', 'key="999"')
    )
    with pytest.raises(ValueError, match="note key"):
        read_dawproject(str(source))
    source_zip(source, xml=PROJECT_XML.replace('id="drums"', 'id="bass"'))
    with pytest.raises(ValueError, match="duplicate DAWproject track"):
        read_dawproject(str(source))


def test_mcp_tools_advertise_incomplete_conversion(tmp_path):
    source = source_zip(tmp_path / "session.dawproject")

    class MCP:
        def __init__(self):
            self.tools = {}

        def tool(self):
            def register(fn):
                self.tools[fn.__name__] = fn
                return fn

            return register

    mcp = MCP()
    register_universal_project_tools(mcp)
    inspected = mcp.tools["fl_inspect_universal_project"](str(source))
    assert inspected["read_only"] is True
    result = mcp.tools["fl_build_reconstruction_bundle"](str(source), str(tmp_path / "output"))
    assert result["native_flp_created"] is False
    repeated = mcp.tools["fl_build_reconstruction_bundle"](str(source), str(tmp_path / "output"))
    assert "FileExistsError" in repeated["error"]


def test_orphan_clip_notes_are_counted_as_unconverted(tmp_path):
    xml = PROJECT_XML.replace('track="bass"', 'track="not-a-real-track"')
    source = source_zip(tmp_path / "orphan.dawproject", xml=xml)
    graph = read_dawproject(str(source))
    assert any(e["kind"] == "unresolved_clip_track" for e in graph["diagnostics"])
    output = tmp_path / "orphan-bundle"
    result = build_dawproject_bundle(str(source), str(output))
    assert result["source_notes"] == 3
    assert result["exported_notes"] == 0
    receipt = json.loads((output / "fidelity_receipt.json").read_text(encoding="utf-8"))
    assert receipt["features"]["notes"]["status"] == "approximated / lossy"


def test_oversized_midi_timestamps_are_declared_skips(tmp_path):
    xml = PROJECT_XML.replace(
        'time="0" duration="8" playStart="0" name="Bass Intro"',
        'time="500000" duration="8" playStart="0" name="Bass Intro"',
    )
    source = source_zip(tmp_path / "long-song.dawproject", xml=xml)
    output = tmp_path / "long-song-bundle"
    result = build_dawproject_bundle(str(source), str(output))
    assert result["source_notes"] == 3
    assert result["exported_notes"] == 1
    receipt = json.loads((output / "fidelity_receipt.json").read_text(encoding="utf-8"))
    assert any(
        item["reason"] == "midi_timestamp_limit" for item in receipt["features"]["notes"]["skipped"]
    )


def test_nested_clip_warps_and_track_hierarchy_survive_in_graph(tmp_path):
    xml = PROJECT_XML.replace(
        '<Track contentType="notes" id="bass" name="Bass">',
        '<Track contentType="notes" id="bass" name="Bass">',
    )
    source = source_zip(tmp_path / "nested.dawproject", xml=xml)
    graph = read_dawproject(str(source))
    audio = next(clip for clip in graph["clips"] if clip["audio"])
    assert audio["warps"] == [
        {"time": "0", "contentTime": "0"},
        {"time": "8", "contentTime": "2.823"},
    ]
    assert len(audio["nested_clips"]) == 1
    assert len(graph["tracks"]) == 3
    output = tmp_path / "nested-bundle"
    build_dawproject_bundle(str(source), str(output))
    receipt = json.loads((output / "fidelity_receipt.json").read_text(encoding="utf-8"))
    assert receipt["features"]["audio_warps"]["source_clips_with_warp_points"] == 1
    assert receipt["features"]["audio_warps"]["status"] == "unknown/unverified in FL Studio"


def test_tempo_automation_explicitly_qualifies_note_timing(tmp_path):
    xml = PROJECT_XML.replace(
        '<Arrangement id="arr">',
        """
    <Arrangement id="arr">
      <TempoAutomation timeUnit="beats" target="tempo">
        <RealPoint time="0" value="149" interpolation="linear"/>
        <RealPoint time="8" value="154" interpolation="linear"/>
      </TempoAutomation>
    """,
    )
    source = source_zip(tmp_path / "tempo.dawproject", xml=xml)
    graph = read_dawproject(str(source))
    assert len(graph["transport"]["tempo_automation"]) == 2
    output = tmp_path / "tempo-bundle"
    build_dawproject_bundle(str(source), str(output))
    receipt = json.loads((output / "fidelity_receipt.json").read_text(encoding="utf-8"))
    assert receipt["features"]["tempo_automation"]["status"] == "unknown/unverified"
    assert receipt["features"]["notes"]["status"] == "approximated / lossy"
    assert "Tempo automation" in receipt["features"]["notes"]["timing_caveat"]
