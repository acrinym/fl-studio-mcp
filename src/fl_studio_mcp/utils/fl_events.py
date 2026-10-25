"""Bounded, read-only FL Studio event-stream inspection.

Event framing follows public PyFLP format documentation; this is NOT a
complete FLP/FST parser. Unknown and opaque plugin payloads are not decoded.
https://pyflp.readthedocs.io/en/stable/architecture/flp-format.html
"""

from __future__ import annotations

import struct
from collections import Counter

MAX_EVENTS = 500_000
MAX_OBSERVED_NAMES = 60

# Only identifiers for which we have a documented, unambiguous meaning.
EVENT_NAMES = {
    9: "project.loop_active",
    10: "project.show_info",
    23: "project.pan_law",
    64: "channel.new",
    65: "pattern.new",
    66: "project.tempo_coarse",
    80: "project.main_pitch_cents",
    93: "project.tempo_fine_raw",
    156: "project.tempo_raw",
    194: "project.title",
    199: "project.fl_version",
    201: "plugin.internal_name",
    203: "plugin.name",
    212: "plugin.wrapper",
    213: "plugin.data",
    223: "pattern.controllers",
    224: "pattern.notes",
}
FORMAT_NAMES = {
    0: "project",
    16: "score",
    24: "automation_state",
    32: "channel_state",
    48: "plugin_state",
    49: "generator_state",
    50: "effect_state",
    64: "mixer_insert_state",
}


def _varint_length(data: bytes, offset: int) -> tuple[int, int]:
    """Read a bounded 7-bit little-endian length without leaving the stream."""
    result = 0
    for index in range(5):  # 128 MiB input cap needs at most four bytes.
        if offset >= len(data):
            raise ValueError("Truncated FL Studio event length")
        value = data[offset]
        offset += 1
        result |= (value & 0x7F) << (7 * index)
        if not value & 0x80:
            if result > len(data) - offset:
                raise ValueError("FL Studio event payload exceeds data chunk")
            return result, offset
    raise ValueError("Overlong FL Studio event length")


def inspect_event_stream(data: bytes, *, format_code: int = 0) -> dict:
    """Count typed FL events and reveal only tightly scoped, documented fields.

    Never expose arbitrary opaque plugin-state bytes, sample paths, or license
    records. All offsets are relative to the FLdt event data chunk.
    """
    if data and not any(data):
        raise ValueError("FL Studio event data is entirely NUL bytes")
    offset = 0
    count = 0
    event_counts: Counter[int] = Counter()
    observed_plugins: list[str] = []
    version = None
    coarse_tempo = None
    project_settings: dict[str, int | bool | str] = {}
    note_blocks = 0
    note_records = 0
    unknown_note_blocks = 0
    opaque_plugin_bytes = 0
    while offset < len(data):
        if count >= MAX_EVENTS:
            raise ValueError("FL Studio event count exceeds inspection limit")
        event_id = data[offset]
        offset += 1
        if event_id < 64:
            size = 1
        elif event_id < 128:
            size = 2
        elif event_id < 192:
            size = 4
        else:
            size, offset = _varint_length(data, offset)
        if size > len(data) - offset:
            raise ValueError("Truncated FL Studio event value")
        start, end = offset, offset + size
        event_counts[event_id] += 1
        count += 1

        if event_id == 199:  # FL version string, not an arbitrary user field.
            version = (
                data[start : min(end, start + 60)]
                .split(b"\x00", 1)[0]
                .decode("ascii", errors="replace")
            )
        elif event_id == 66 and format_code == 0:
            # Two-byte coarse tempo; automation or fine-tempo events may override.
            coarse_tempo = struct.unpack_from("<H", data, start)[0]
            project_settings["coarse_tempo_bpm"] = coarse_tempo
        elif format_code == 0 and event_id in (9, 10):
            project_settings["loop_active" if event_id == 9 else "show_info"] = bool(data[start])
        elif format_code == 0 and event_id == 23:
            pan_law = data[start]
            project_settings["pan_law"] = {0: "circular", 2: "triangular"}.get(
                pan_law, f"unknown ({pan_law})"
            )
        elif format_code == 0 and event_id == 80:
            project_settings["main_pitch_cents"] = struct.unpack_from("<h", data, start)[0]
        elif event_id in (201, 203) and len(observed_plugins) < MAX_OBSERVED_NAMES:
            raw = data[start : min(end, start + 160)].split(b"\x00", 1)[0]
            label = raw.decode("utf-8", errors="replace").strip()
            if label and all(ch.isprintable() for ch in label) and label not in observed_plugins:
                observed_plugins.append(label)
        elif event_id == 213:
            opaque_plugin_bytes += size
        elif event_id == 224:
            note_blocks += 1
            if size % 24 == 0:
                note_records += size // 24
            else:
                unknown_note_blocks += 1
        offset = end

    return {
        "event_count": count,
        "event_type_counts": {
            EVENT_NAMES.get(key, f"event_{key}"): value
            for key, value in sorted(event_counts.items())
        },
        "channel_new_events": event_counts[64],
        "pattern_new_events": event_counts[65],
        "note_blocks": note_blocks,
        "note_records_24_byte_layout": note_records,
        "note_blocks_unrecognized_layout": unknown_note_blocks,
        "plugin_data_events": event_counts[213],
        "opaque_plugin_data_bytes": opaque_plugin_bytes,
        "observed_plugin_labels": observed_plugins,
        "observed_plugin_labels_truncated": len(observed_plugins) == MAX_OBSERVED_NAMES,
        "fl_version": version,
        "coarse_tempo_bpm": coarse_tempo if format_code == 0 else None,
        "project_settings": project_settings if format_code == 0 else None,
        "read_only": True,
        "note": "Event counts are structural hints, not a complete project graph.",
    }
