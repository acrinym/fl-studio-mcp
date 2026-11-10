"""Offline, explicitly templated FLP note-and-pattern reconstruction.

Optional MIT-licensed flpkit backend. A user-supplied FL-authored .flp is
copied into a new output folder; the original is never patched. Only
qualified note clips are written and verified by a second independent read
of the new bytes. Native FL Studio application validation remains pending.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from collections import Counter
from pathlib import Path

from fl_studio_mcp.utils.universal_project import read_dawproject

MAX_TEMPLATE_BYTES = 128 * 1024 * 1024
SCHEMA = "fl-studio-native-template-reconstruction/1"


def _sha(path: Path) -> str:
    """Hash a template, source, or output without reading it all at once."""
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _source_feature_inventory(graph: dict) -> dict:
    """Account for native-app semantics we cannot reconstruct, not only notes."""
    return {
        "source_tracks": len(graph["tracks"]),
        "source_static_time_signature": graph["transport"]["signature"],
        "source_clips": len(graph["clips"]),
        "source_notes": sum(len(c["notes"]) for c in graph["clips"]),
        "audio_clips": sum(bool(c["audio"]) for c in graph["clips"]),
        "warped_clips": sum(bool(c["warps"]) for c in graph["clips"]),
        "nested_clip_envelopes": sum(bool(c["nested_clips"]) for c in graph["clips"]),
        "plugin_devices": sum(len(t["devices"]) for t in graph["tracks"]),
        "routing_destinations": sum(bool(t["destination"]) for t in graph["tracks"]),
        "aux_sends": sum(len(t["sends"]) for t in graph["tracks"]),
        "automation_lanes": len(graph["automation"]),
        "tempo_automation_points": len(graph["transport"]["tempo_automation"]),
        "markers": len(graph["markers"]),
        "embedded_resources": sum(bool(r["available"]) for r in graph["resources"]),
        "unavailable_resources": sum(not r["available"] for r in graph["resources"]),
        "external_resource_refs": sum(
            d["kind"] == "external_resource_not_read" for d in graph["diagnostics"]
        ),
    }


def _note_clips(graph: dict) -> tuple[list[dict], list[dict]]:
    """Select reconstructable note clips without misrepresenting timing."""
    supported: list[dict] = []
    skipped: list[dict] = []
    for clip_index, clip in enumerate(graph["clips"]):
        if not clip["notes"]:
            continue
        errors = []
        if clip["time_unit"] != "beats" or clip["content_time_unit"] != "beats":
            errors.append("unsupported_time_unit")
        if clip["loop_start"] is not None or clip["loop_end"] is not None:
            errors.append("loop_expansion_not_verified")
        if clip["reference"] is not None:
            errors.append("clip_reference_not_resolved")
        if clip["nested_clips"]:
            errors.append("nested_clip_timing_not_resolved")
        if clip["play_start"] != 0:
            errors.append("nonzero_play_start_not_reconstructed")
        if clip["start"] < 0 or clip["duration"] <= 0:
            errors.append("invalid_clip_window")
        if not any(t["id"] == clip["track_id"] for t in graph["tracks"]):
            errors.append("orphan_clip_track")
        if errors:
            skipped.append(
                {
                    "track_id": clip["track_id"],
                    "clip": clip["name"],
                    "source_notes": len(clip["notes"]),
                    "reasons": errors,
                }
            )
            continue
        valid_notes = []
        valid_indices = []
        for note_index, note in enumerate(clip["notes"]):
            if (
                note["channel"] != 0
                or note["velocity"] <= 0
                or note["time"] < 0
                or note["duration"] <= 0
                or note["time"] + note["duration"] > clip["duration"]
            ):
                skipped.append(
                    {
                        "track_id": clip["track_id"],
                        "clip": clip["name"],
                        "source_notes": 1,
                        "reasons": ["note_requires_unmapped_semantics"],
                    }
                )
                continue
            valid_notes.append(note)
            valid_indices.append(note_index)
        if valid_notes:
            supported.append(
                {
                    "clip": clip,
                    "clip_index": clip_index,
                    "notes": valid_notes,
                    "note_indices": valid_indices,
                }
            )
    return supported, skipped


def _readback_matches(project, assignments: list[dict], ppq: int) -> None:
    """Reject any FLP whose parsed notes and playlist depart from the plan."""
    expected_clips = Counter()
    expected_notes = Counter()
    for entry in assignments:
        expected_clips[
            (
                round(entry["clip"]["start"] * ppq),
                round(entry["clip"]["duration"] * ppq),
                entry["playlist_track"],
                entry["pattern"],
            )
        ] += 1
        for note in entry["notes"]:
            expected_notes[
                (
                    entry["pattern"],
                    entry["channel"],
                    note["key"],
                    round(note["time"] * ppq),
                    round(note["duration"] * ppq),
                    round(note["velocity"] * 127),
                )
            ] += 1
    actual_clips = Counter(
        (p.position, p.length, p.track, p.pattern)
        for p in project.playlist
        if p.pattern is not None and p.arrangement == 0
    )
    actual_notes = Counter(
        (n.pattern, n.channel, n.key, n.position, n.length, n.velocity) for n in project.notes
    )
    if actual_clips != expected_clips:
        raise ValueError(
            "Native FLP offline read-back: playlist differs from declared source mapping"
        )
    if actual_notes != expected_notes:
        raise ValueError("Native FLP offline read-back: note timing/identity differs from mapping")
    if len(project.playlist) != len(assignments):
        raise ValueError("Native FLP offline read-back: extra or missing Playlist items")
    if len(project.notes) != sum(len(a["notes"]) for a in assignments):
        raise ValueError("Native FLP offline read-back: duplicate or extra notes")


def build_native_flp_from_template(source_path: str, template_path: str, output_dir: str) -> dict:
    """Project selected DAWproject notes and clips into a NEW FLP from a scaffold.

    The template must contain empty channel slots and existing pattern-clip
    playlist records. No installed DAW or plugin is loaded. Only offline
    parser-level readback is possible without FL Studio runtime permission.
    """
    try:
        import flpkit  # MIT, optional: no GPL parser/writer or plugin code
    except ImportError as exc:
        raise RuntimeError(
            "Native FLP backend requires optional flpkit==0.9.0 on Python 3.11+"
        ) from exc

    source = Path(source_path).expanduser().resolve(strict=True)
    template = Path(template_path).expanduser().resolve(strict=True)
    destination = Path(output_dir).expanduser().resolve(strict=False)
    if not template.is_file() or template.suffix.lower() != ".flp":
        raise ValueError("Native FLP template must be a regular .flp file")
    if template.stat().st_size > MAX_TEMPLATE_BYTES:
        raise ValueError("FLP template exceeds size bound")
    if destination.exists():
        raise FileExistsError("Refusing to overwrite a native FLP reconstruction directory")
    if not destination.parent.is_dir():
        raise FileNotFoundError("Native FLP reconstruction parent directory missing")
    graph = read_dawproject(str(source))
    source_sha, template_sha = graph["source"]["sha256"], _sha(template)
    baseline = flpkit.read(template)
    if baseline.trial_saved:
        raise ValueError("Trial-saved FLP templates are not eligible for reconstruction")
    if baseline.notes:
        raise ValueError("Template must have empty note patterns; refuse inherited note content")
    if any(item.pattern is None for item in baseline.playlist):
        raise ValueError("Template has non-pattern playlist items; cannot preserve them as-is")
    if any(c.sample_path or c.plugin or c.automation for c in baseline.channels):
        raise ValueError(
            "Template has samples, plugins or channel automation; require a blank scaffold"
        )
    if baseline.mixer:
        raise ValueError("Template has existing mixer effects; refuse nonblank source template")
    if any(p.arrangement != 0 for p in baseline.playlist):
        raise ValueError("Template contains other arrangements; require a blank scaffold")
    if baseline.ppq <= 0:
        raise ValueError("Template has invalid PPQ")
    patterns = sorted({p.pattern for p in baseline.playlist if p.pattern is not None})
    playlist_tracks = sorted({p.track for p in baseline.playlist if p.pattern is not None})
    channels = [c.index for c in baseline.channels if c.kind in (None, 0) and not c.sample_path]
    candidates, skipped = _note_clips(graph)
    if not candidates:
        raise ValueError("Source contains no eligible note clips for native FLP reconstruction")
    source_track_ids = list(dict.fromkeys(c["clip"]["track_id"] for c in candidates))
    if len(candidates) > len(patterns):
        raise ValueError("Template lacks enough distinct pre-existing pattern scaffolds")
    if len(source_track_ids) > len(playlist_tracks):
        raise ValueError("Template lacks enough pattern Playlist track scaffolds")
    if len(source_track_ids) > len(channels):
        raise ValueError("Template lacks enough empty instrument channel scaffolds")

    track_slot = dict(zip(source_track_ids, playlist_tracks))
    channel_slot = dict(zip(source_track_ids, channels))
    assigned: list[dict] = []
    quantized = []
    for idx, element in enumerate(candidates):
        clip = element["clip"]
        p = patterns[idx]
        c = channel_slot[clip["track_id"]]
        t = track_slot[clip["track_id"]]
        for note in element["notes"]:
            for value in (note["time"], note["duration"]):
                if abs(round(value * baseline.ppq) - value * baseline.ppq) > 1e-9:
                    quantized.append({"clip": clip["name"], "note": note["key"]})
        if any(
            abs(round(v * baseline.ppq) - v * baseline.ppq) > 1e-9
            for v in (clip["start"], clip["duration"])
        ):
            quantized.append({"clip": clip["name"], "reason": "clip_timebase_rounding"})
        assigned.append(
            {
                "clip": clip,
                "clip_index": element["clip_index"],
                "notes": element["notes"],
                "note_indices": element["note_indices"],
                "pattern": p,
                "channel": c,
                "playlist_track": t,
            }
        )
    staging = Path(tempfile.mkdtemp(prefix=".fl-native-", dir=destination.parent))
    complete = False
    try:
        result_path = staging / "reconstructed.flp"
        shutil.copyfile(template, result_path)
        flpkit.set_tempo(result_path, graph["transport"]["bpm"])
        for entry in assigned:
            notes = [
                flpkit.NoteSpec(
                    key=n["key"],
                    start=n["time"],
                    length=n["duration"],
                    velocity=n["velocity"],
                )
                for n in entry["notes"]
            ]
            flpkit.write_notes(
                result_path,
                notes,
                pattern=entry["pattern"],
                channel=entry["channel"],
                mode="replace",
            )
        clip_specs = [
            flpkit.ClipSpec(
                pattern=e["pattern"],
                track=e["playlist_track"],
                start=e["clip"]["start"],
                length=e["clip"]["duration"],
            )
            for e in assigned
        ]
        flpkit.write_playlist(result_path, clip_specs, mode="replace", arrangement=0)
        verified = flpkit.read(result_path)
        _readback_matches(verified, assigned, baseline.ppq)
        if verified.tempo is None or abs(verified.tempo - graph["transport"]["bpm"]) > 0.01:
            raise ValueError("Native FLP offline read-back: tempo mismatch")
        if _sha(source) != source_sha or _sha(template) != template_sha:
            raise ValueError("Source or native template changed during reconstruction")
        graph_path = staging / "project_graph.json"
        graph_path.write_text(
            json.dumps(graph, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        source_notes = sum(len(clip["notes"]) for clip in graph["clips"])
        exported_notes = sum(len(item["notes"]) for item in assigned)
        expression_count = sum(
            bool(note.get("release")) or bool(note.get("expressions"))
            for clip in graph["clips"]
            for note in clip["notes"]
        )
        receipts = {
            "schema": SCHEMA,
            "source_sha256": source_sha,
            "project_graph_sha256": _sha(graph_path),
            "template_sha256": template_sha,
            "output_flp_sha256": _sha(result_path),
            "native_flp_created": True,
            "offline_flp_readback_verified": True,
            "fl_studio_open_verified": False,
            "fl_studio_resave_verified": False,
            "complete_automatic_conversion": False,
            "backend": "flpkit==0.9.0 (MIT); FL-authored existing scaffold only",
            "source_notes": source_notes,
            "exported_notes": exported_notes,
            "source_feature_inventory": _source_feature_inventory(graph),
            "mapped_feature_inventory": {
                "native_note_patterns": len(assigned),
                "native_playlist_clips": len(assigned),
                "native_mapped_notes": exported_notes,
                "native_mapped_source_tracks": len(source_track_ids),
                "native_static_tempo": 1,
                "native_time_signature_written": False,
                "native_audio_clips": 0,
                "native_plugin_devices": 0,
                "native_automation_lanes": 0,
                "native_routing_destinations": 0,
            },
            "source_notes_with_expression_or_release": expression_count,
            "note_status": "approximated / lossy"
            if (
                source_notes != exported_notes
                or quantized
                or expression_count
                or graph["transport"]["tempo_automation"]
            )
            else "converted with declared mapping (offline only)",
            "mapped_clips": [
                {
                    "source_track_id": x["clip"]["track_id"],
                    "source_clip": x["clip"]["name"],
                    "source_clip_index": x["clip_index"],
                    "source_note_indices": x["note_indices"],
                    "pattern": x["pattern"],
                    "channel": x["channel"],
                    "playlist_track": x["playlist_track"],
                    "notes": len(x["notes"]),
                }
                for x in assigned
            ],
            "skipped": skipped,
            "timebase_quantized": quantized,
            "unverified_features": [
                "Actual FL Studio application open/save/reopen",
                "Device/plugin identities, presets and vendor-state portability",
                "Audio clip placement, warp/stretch and sample dependencies",
                "Mixer routing, sends, effects, automation and time-varying tempo",
                "Native Playlist group hierarchy and source FL channel semantics",
                "Note release/MPE expression and nonzero MIDI note channels",
            ],
            "diagnostics": graph["diagnostics"],
            "template_fidelity": (
                "Existing template copied; only selected FLP note, playlist and tempo "
                "events patched"
            ),
        }
        (staging / "native_fidelity_receipt.json").write_text(
            json.dumps(receipts, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        # Independent post-generation audit must pass before publishing output.
        from fl_studio_mcp.utils.native_flp_audit import audit_native_flp_bundle

        audit_native_flp_bundle(str(staging), str(source))
        os.replace(staging, destination)
        complete = True
        return {
            "native_flp": str(destination / "reconstructed.flp"),
            "receipt": str(destination / "native_fidelity_receipt.json"),
            "source_notes": source_notes,
            "exported_notes": exported_notes,
            "native_flp_created": True,
            "offline_flp_readback_verified": True,
            "fl_studio_open_verified": False,
            "complete_automatic_conversion": False,
        }
    finally:
        if not complete:
            shutil.rmtree(staging, ignore_errors=True)
