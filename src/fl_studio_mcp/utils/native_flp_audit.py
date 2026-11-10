"""Independent, read-only semantic audit for native FLP reconstruction bundles.

It does not trust a success flag in a receipt: it re-parses the source, hashes
three artifacts, rebuilds the declared mapping from source indices, and re-reads
the FLP independently. This cannot establish real FL Studio app acceptance.
"""

from __future__ import annotations

import json
from pathlib import Path

from fl_studio_mcp.utils.native_flp_meter import read_native_meter
from fl_studio_mcp.utils.native_flp_template import (
    SCHEMA,
    _note_clips,
    _readback_matches,
    _sha,
    _source_feature_inventory,
)
from fl_studio_mcp.utils.universal_project import read_dawproject

MAX_RECEIPT_BYTES = 8 * 1024 * 1024
MAX_GRAPH_BYTES = 64 * 1024 * 1024
MAX_FL_BYTES = 256 * 1024 * 1024


def _checked_file(root: Path, name: str, limit: int) -> Path:
    """Reject missing, linked, or overlarge bundled content before opening it."""
    path = root / name
    if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
        raise ValueError(f"Invalid native FLP reconstruction artifact: {name}")
    return path


def audit_native_flp_bundle(output_dir: str, source_path: str) -> dict:
    """Independently compare a generated native FLP to its original DAWproject.

    Requires explicit paths to an existing bundle and source. Never reads
    external sample references, never changes either file, and never starts
    FL Studio. A receipt cannot establish native application acceptance.
    """
    try:
        import flpkit
    except ImportError as exc:
        raise RuntimeError("Native FLP audit requires optional flpkit==0.9.0") from exc

    root = Path(output_dir).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Native FLP reconstruction path must be a directory")
    source = Path(source_path).expanduser().resolve(strict=True)
    if not source.is_file():
        raise ValueError("Original DAWproject must be a regular file")
    receipt_path = _checked_file(root, "native_fidelity_receipt.json", MAX_RECEIPT_BYTES)
    graph_path = _checked_file(root, "project_graph.json", MAX_GRAPH_BYTES)
    flp_path = _checked_file(root, "reconstructed.flp", MAX_FL_BYTES)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    graph = json.loads(graph_path.read_text(encoding="utf-8"))
    if not isinstance(receipt, dict) or not isinstance(graph, dict):
        raise ValueError("Native FLP receipt/graph must be JSON objects")
    if receipt.get("schema") != SCHEMA:
        raise ValueError("Native FLP receipt schema mismatch")
    if (
        receipt.get("fl_studio_open_verified") is not False
        or receipt.get("fl_studio_resave_verified") is not False
        or receipt.get("complete_automatic_conversion") is not False
    ):
        raise ValueError("Receipt makes unverified FL Studio application success claims")
    if receipt.get("native_flp_created") is not True:
        raise ValueError("Receipt does not declare a completed native FLP")
    if receipt.get("project_graph_sha256") != _sha(graph_path):
        raise ValueError("Project graph hash mismatches native fidelity receipt")
    if receipt.get("output_flp_sha256") != _sha(flp_path):
        raise ValueError("Native FLP hash mismatches native fidelity receipt")

    actual_source = read_dawproject(str(source))
    if graph != actual_source:
        raise ValueError("Saved graph differs from the original parsed DAWproject")
    if (
        receipt.get("source_sha256") != _sha(source)
        or receipt["source_sha256"] != graph["source"]["sha256"]
    ):
        raise ValueError("Native FLP source SHA-256 mismatch")

    candidates, skipped = _note_clips(graph)
    declared = receipt.get("mapped_clips")
    if not isinstance(declared, list) or len(declared) != len(candidates):
        raise ValueError("Receipt has incorrect mapped clip count")
    if receipt.get("skipped") != skipped:
        raise ValueError("Receipt omits or alters skipped source note evidence")
    if receipt.get("source_feature_inventory") != _source_feature_inventory(graph):
        raise ValueError("Native receipt omits source features")

    assigned = []
    slots: dict[str, tuple[int, int]] = {}
    distinct_patterns: set[int] = set()
    for candidate, item in zip(candidates, declared):
        if not isinstance(item, dict):
            raise ValueError("Mapped clip receipt must contain objects")
        clip = candidate["clip"]
        if (
            item.get("source_clip_index") != candidate["clip_index"]
            or item.get("source_note_indices") != candidate["note_indices"]
            or item.get("source_track_id") != clip["track_id"]
            or item.get("source_clip") != clip["name"]
            or item.get("notes") != len(candidate["notes"])
        ):
            raise ValueError("Receipt mapping does not match source clip identity/notes")
        pattern, channel, playlist_track = (
            item.get("pattern"),
            item.get("channel"),
            item.get("playlist_track"),
        )
        if (
            type(pattern) is not int
            or not 1 <= pattern <= 65535
            or type(channel) is not int
            or not 0 <= channel <= 65535
            or type(playlist_track) is not int
            or not 0 <= playlist_track <= 65535
        ):
            raise ValueError("Native FLP mapping contains invalid pattern/channel/track")
        if pattern in distinct_patterns:
            raise ValueError("Source clips are assigned overlapping native patterns")
        distinct_patterns.add(pattern)
        track_slots = (channel, playlist_track)
        track_id = clip["track_id"]
        if track_id in slots and slots[track_id] != track_slots:
            raise ValueError("A source track has inconsistent FLP channel or Playlist mapping")
        slots[track_id] = track_slots
        assigned.append(
            {
                "clip": clip,
                "notes": candidate["notes"],
                "pattern": pattern,
                "channel": channel,
                "playlist_track": playlist_track,
            }
        )

    project = flpkit.read(flp_path)
    _readback_matches(project, assigned, project.ppq)
    source_notes = sum(len(c["notes"]) for c in graph["clips"])
    exported_notes = sum(len(item["notes"]) for item in assigned)
    if (
        receipt.get("source_notes") != source_notes
        or receipt.get("exported_notes") != exported_notes
    ):
        raise ValueError("Receipt source/exported note accounting mismatch")
    expected_mapped_inventory = {
        "native_note_patterns": len(assigned),
        "native_playlist_clips": len(assigned),
        "native_mapped_notes": exported_notes,
        "native_mapped_source_tracks": len(slots),
        "native_static_tempo": 1,
        "native_time_signature_written": True,
        "native_audio_clips": 0,
        "native_plugin_devices": 0,
        "native_automation_lanes": 0,
        "native_routing_destinations": 0,
    }
    if receipt.get("mapped_feature_inventory") != expected_mapped_inventory:
        raise ValueError("Native receipt misstates the source-to-FLP feature inventory")
    source_expression_notes = sum(
        bool(n.get("release")) or bool(n.get("expressions"))
        for clip in graph["clips"]
        for n in clip["notes"]
    )
    if receipt.get("source_notes_with_expression_or_release") != source_expression_notes:
        raise ValueError("Receipt omits source note expression/release evidence")
    if project.tempo is None or abs(project.tempo - graph["transport"]["bpm"]) > 0.01:
        raise ValueError("Native FLP tempo differs from the source project")
    if read_native_meter(flp_path) != tuple(graph["transport"]["signature"]):
        raise ValueError("Native FLP time signature differs from the source project")

    quantized = []
    for entry in assigned:
        clip = entry["clip"]
        for note in entry["notes"]:
            for value in (note["time"], note["duration"]):
                if abs(round(value * project.ppq) - value * project.ppq) > 1e-9:
                    quantized.append({"clip": clip["name"], "note": note["key"]})
        if any(
            abs(round(v * project.ppq) - v * project.ppq) > 1e-9
            for v in (clip["start"], clip["duration"])
        ):
            quantized.append({"clip": clip["name"], "reason": "clip_timebase_rounding"})
    if receipt.get("timebase_quantized") != quantized:
        raise ValueError("Receipt does not report the actual FLP note/clip timing quantization")
    expected_status = (
        "approximated / lossy"
        if (
            source_notes != exported_notes
            or quantized
            or source_expression_notes
            or graph["transport"]["tempo_automation"]
        )
        else "converted with declared mapping (offline only)"
    )
    if receipt.get("note_status") != expected_status:
        raise ValueError("Receipt claims unsupported note fidelity")
    if _sha(source) != graph["source"]["sha256"] or _sha(flp_path) != receipt["output_flp_sha256"]:
        raise ValueError("Source/FLP changed while verifying the offline reconstruction")
    return {
        "verified": True,
        "verification_scope": "source-to-native-FLP structure, OFFLINE only",
        "source_sha256": receipt["source_sha256"],
        "output_flp_sha256": receipt["output_flp_sha256"],
        "source_notes": source_notes,
        "verified_notes": exported_notes,
        "verified_clips": len(assigned),
        "skipped_source_notes": source_notes - exported_notes,
        "fl_studio_open_verified": False,
        "fl_studio_resave_verified": False,
        "complete_automatic_conversion": False,
    }
