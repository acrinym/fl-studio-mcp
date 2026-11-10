"""Non-destructive FL Studio reconstruction bundle for DAWproject sources.

A bundle contains valid importable MIDI timelines, exact embedded media/state
copies, a versioned complete source graph and a loss/accounting receipt. It is
NOT an FLP and must not be represented as automatic DAW reconstruction.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import zipfile
from pathlib import Path

import mido

from fl_studio_mcp.utils.universal_project import _resource_path, read_dawproject

TICKS_PER_BEAT = 960
MAX_MIDI_TICK = 0x0FFFFFFF
BUNDLE_VERSION = "fl-studio-reconstruction-bundle/1"


def _json(path: Path, obj: dict) -> None:
    """Persist a deterministic UTF-8 JSON artifact with stable key ordering."""
    path.write_text(
        json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _sha(path: Path) -> str:
    """Hash an output file for comparison with its source provenance."""
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _events_for_track(graph: dict, track_id: str) -> tuple[list, dict]:
    """Emit events only when clip timing is unambiguous; no fabricated loops."""
    events = []
    skipped = []
    total = 0
    for clip in graph["clips"]:
        if clip["track_id"] != track_id:
            continue
        total += len(clip["notes"])
        if not clip["notes"]:
            continue
        if clip["time_unit"] != "beats" or clip["content_time_unit"] != "beats":
            skipped.append({"reason": "unsupported_time_unit", "clip": clip["name"]})
            continue
        if clip["loop_start"] is not None or clip["loop_end"] is not None:
            skipped.append({"reason": "loop_expansion_not_verified", "clip": clip["name"]})
            continue
        if clip["reference"] is not None:
            skipped.append({"reason": "clip_reference_not_resolved", "clip": clip["name"]})
            continue
        if clip["nested_clips"]:
            # Nested clip offsets and play windows are not yet translated;
            # exporting their descendant notes at the outer start is false fidelity.
            skipped.append({"reason": "nested_clip_timing_not_resolved", "clip": clip["name"]})
            continue
        for note in clip["notes"]:
            start = clip["start"] + note["time"] - clip["play_start"]
            end = start + note["duration"]
            clip_start, clip_end = clip["start"], clip["start"] + clip["duration"]
            # FL clip boundaries clip note playback. Preserve that on export.
            start, end = max(start, clip_start), min(end, clip_end)
            if start >= end:
                skipped.append({"reason": "outside_clip_window", "clip": clip["name"]})
                continue
            if note["velocity"] == 0:
                skipped.append({"reason": "silent_velocity_zero", "clip": clip["name"]})
                continue
            a = int(round(start * TICKS_PER_BEAT))
            b = int(round(end * TICKS_PER_BEAT))
            if b <= a:
                skipped.append({"reason": "sub_tick_note", "clip": clip["name"]})
                continue
            if b > MAX_MIDI_TICK:
                skipped.append({"reason": "midi_timestamp_limit", "clip": clip["name"]})
                continue
            velocity = max(1, min(127, round(note["velocity"] * 127)))
            events.append((a, 1, "note_on", note["key"], velocity, note["channel"]))
            events.append((b, 0, "note_off", note["key"], 0, note["channel"]))
    events.sort()
    return events, {"source_notes": total, "exported_notes": len(events) // 2, "skipped": skipped}


def _write_midi(path: Path, track: dict, events: list, bpm: float, signature: list) -> None:
    """Write declared-position MIDI note events with source tempo and meter."""
    midi = mido.MidiFile(type=0, ticks_per_beat=TICKS_PER_BEAT)
    midi_track = mido.MidiTrack()
    midi.tracks.append(midi_track)
    midi_track.append(mido.MetaMessage("track_name", name=track["name"] or track["id"], time=0))
    midi_track.append(mido.MetaMessage("set_tempo", tempo=round(60_000_000 / bpm), time=0))
    midi_track.append(
        mido.MetaMessage("time_signature", numerator=signature[0], denominator=signature[1], time=0)
    )
    last = 0
    for tick, _, kind, key, vel, channel in events:
        midi_track.append(
            mido.Message(
                kind,
                note=key,
                velocity=vel,
                channel=channel,
                time=tick - last,
            )
        )
        last = tick
    midi.save(path)


def build_dawproject_bundle(source_path: str, output_dir: str) -> dict:
    """Write a new separate bundle; refuse overwrite; never launch the DAW.

    All work is staged in a temporary sibling directory and then atomically
    moved into the requested previously absent destination.
    """
    source = Path(source_path).expanduser().resolve(strict=True)
    graph = read_dawproject(str(source))
    dest = Path(output_dir).expanduser().resolve(strict=False)
    if dest.exists():
        raise FileExistsError("Refusing to overwrite an existing reconstruction bundle")
    if not dest.parent.is_dir():
        raise FileNotFoundError("Reconstruction bundle parent directory does not exist")
    staged = Path(tempfile.mkdtemp(prefix=".fl-rebuild-", dir=dest.parent))
    committed = False
    try:
        _json(staged / "project_graph.json", graph)
        (staged / "midi").mkdir()
        (staged / "resources").mkdir()
        exported = []
        mapped = 0
        # Count every source note, including clips with missing/unknown track IDs.
        # Otherwise orphaned notes vanish from the conversion receipt.
        source_notes = sum(len(clip["notes"]) for clip in graph["clips"])
        skipped_notes = []
        for index, track in enumerate(graph["tracks"]):
            events, stats = _events_for_track(graph, track["id"])
            skipped_notes.extend({"track_id": track["id"], **item} for item in stats["skipped"])
            if not events:
                continue
            name = f"track-{index:04d}.mid"
            path = staged / "midi" / name
            _write_midi(
                path, track, events, graph["transport"]["bpm"], graph["transport"]["signature"]
            )
            mapped += stats["exported_notes"]
            exported.append(
                {
                    "track_id": track["id"],
                    "track_name": track["name"],
                    "path": f"midi/{name}",
                    "notes": stats["exported_notes"],
                    "sha256": _sha(path),
                    "arrangement": "flattened into one positioned MIDI timeline per source track",
                }
            )
        files = []
        with zipfile.ZipFile(source) as archive:
            for entry in graph["resources"]:
                if not entry["available"]:
                    files.append({"path": entry["path"], "status": "unavailable/missing"})
                    continue
                path = _resource_path(entry["path"])
                target = staged / "resources" / Path(*path.split("/"))
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(path) as src, target.open("xb") as dst:
                    shutil.copyfileobj(src, dst, length=1024 * 1024)
                if _sha(target) != entry["sha256"]:
                    raise ValueError("Source resource changed or became corrupt during copy")
                files.append(
                    {
                        "source_path": path,
                        "bundle_path": f"resources/{path}",
                        "bytes": entry["bytes"],
                        "sha256": entry["sha256"],
                        "status": "preserved exactly (bytes); FL Studio mapping unverified",
                    }
                )
        # Keep unsupported details available in project_graph.json rather than
        # dropping them from the conversion or inventing equivalent FL controls.
        devices = sum(len(track["devices"]) for track in graph["tracks"])
        audio_clips = sum(bool(clip["audio"]) for clip in graph["clips"])
        external_resources = [
            issue for issue in graph["diagnostics"] if issue["kind"] == "external_resource_not_read"
        ]
        tempo_changes = len(graph["transport"]["tempo_automation"])
        notes_fidelity = (
            "converted with declared mapping"
            if mapped == source_notes and tempo_changes == 0
            else "approximated / lossy"
        )
        expression_count = sum(
            bool(note["release"]) or bool(note.get("expressions"))
            for clip in graph["clips"]
            for note in clip["notes"]
        )
        warped_audio = sum(bool(clip.get("warps")) for clip in graph["clips"])
        receipt = {
            "schema": BUNDLE_VERSION,
            "source": graph["source"],
            "graph_schema": graph["schema"],
            "destination": "FL Studio reconstruction assistance (NOT native FLP)",
            "native_flp_created": False,
            "fl_studio_open_verified": False,
            "complete_automatic_conversion": False,
            "features": {
                "tempo_static": {
                    "status": "converted with declared mapping",
                    "bpm": graph["transport"]["bpm"],
                },
                "tempo_automation": {
                    "status": "unknown/unverified"
                    if graph["transport"]["tempo_automation"]
                    else "not present",
                    "points": len(graph["transport"]["tempo_automation"]),
                },
                "time_signature_static": {
                    "status": "converted with declared mapping",
                    "value": graph["transport"]["signature"],
                },
                "notes": {
                    "status": notes_fidelity,
                    "source": source_notes,
                    "exported": mapped,
                    "skipped": skipped_notes,
                    "timing_caveat": "Tempo automation is not mapped to MIDI tempos"
                    if tempo_changes
                    else None,
                },
                "note_expressions": {
                    "status": "unknown/unverified in FL Studio"
                    if expression_count
                    else "not present",
                    "source_notes_with_expression_or_release": expression_count,
                },
                "audio_warps": {
                    "status": "unknown/unverified in FL Studio" if warped_audio else "not present",
                    "source_clips_with_warp_points": warped_audio,
                },
                "clip_instances": {
                    "status": "approximated / lossy",
                    "reason": "MIDI imports are timelines, not native FL Studio Playlist clips",
                    "count": len(graph["clips"]),
                },
                "embedded_resources": {
                    "status": "preserved exactly (bytes) when available",
                    "files": files,
                },
                "audio_clips": {"status": "unknown/unverified in FL Studio", "count": audio_clips},
                "external_resources": {
                    "status": "unavailable/missing" if external_resources else "not present",
                    "count": len(external_resources),
                    "reason": "Host paths are never read or copied",
                },
                "devices": {"status": "unknown/unverified in FL Studio", "count": devices},
                "automation": {
                    "status": "unknown/unverified in FL Studio",
                    "lanes": len(graph["automation"]),
                },
                "routing": {
                    "status": "unknown/unverified in FL Studio",
                    "routes": sum(bool(t["destination"]) for t in graph["tracks"]),
                    "aux_sends": sum(len(t.get("sends", [])) for t in graph["tracks"]),
                },
            },
            "diagnostics": graph["diagnostics"],
            "required_manual_steps": [
                "Create a NEW blank FL Studio project, never overwrite the source.",
                "Import MIDI timelines, confirm note counts and positions; "
                "do not assume source clips were rebuilt.",
                "Place audio clips using project_graph.json start/duration and bundled resources.",
                "Rebuild routing, plugins, device state and automation from the graph; "
                "inspect each in FL Studio.",
                "Save a new FLP separately; reopen and audit it before claiming native conversion.",
            ],
        }
        _json(staged / "fidelity_receipt.json", receipt)
        if _sha(source) != graph["source"]["sha256"]:
            raise ValueError("Source project changed during reconstruction")
        os.replace(staged, dest)
        committed = True
        return {
            "bundle": str(dest),
            "source_sha256": graph["source"]["sha256"],
            "midi_files": exported,
            "resources": len(files),
            "source_notes": source_notes,
            "exported_notes": mapped,
            "native_flp_created": False,
            "fl_studio_open_verified": False,
            "receipt": str(dest / "fidelity_receipt.json"),
        }
    finally:
        if not committed:
            shutil.rmtree(staged)
