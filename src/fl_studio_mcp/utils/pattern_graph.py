"""Read-only relationships between FL Studio pattern IDs, channels, and notes.

This is a deliberately narrow project graph, not a replacement for FL Studio's
project model. The public 24-byte note record layout is documented at
https://pyflp.readthedocs.io/en/stable/reference/patterns/pattern.html
No third-party parser code is imported.
"""

from __future__ import annotations

import struct
from collections import Counter

from fl_studio_mcp.utils.fl_events import MAX_EVENTS, _varint_length

_NOTE = struct.Struct("<IHHIHH8B")
MAX_NOTE_RESULT = 4096
MAX_PATTERN_RESULT = 256


def inspect_pattern_note_graph(data: bytes, *, ppq: int, max_notes: int = 1024) -> dict:
    """Attribute note records to pattern IDs and rack-channel identifiers.

    This does not infer a track's sample/plugin, playlist placement, or mixer
    assignment. Channel ID is the ID encoded in each note, not an array index.
    """
    if type(max_notes) is not int or not 0 <= max_notes <= MAX_NOTE_RESULT:
        raise ValueError("max_notes must be an integer from 0 through 4096")
    offset = 0
    event_count = 0
    current_pattern: int | None = None
    patterns: dict[int, dict] = {}
    seen_pattern_ids: set[int] = set()
    orphans = 0
    returned = 0
    total = 0
    extra_patterns = 0

    while offset < len(data):
        if event_count >= MAX_EVENTS:
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
        event_count += 1
        if event_id == 65:
            current_pattern = struct.unpack_from("<H", data, start)[0]
            if current_pattern not in seen_pattern_ids:
                seen_pattern_ids.add(current_pattern)
                if len(patterns) >= MAX_PATTERN_RESULT:
                    extra_patterns += 1
                else:
                    patterns[current_pattern] = {
                        "pattern_id": current_pattern,
                        "note_count": 0,
                        "channel_note_counts": Counter(),
                        "notes": [],
                    }
        elif event_id == 224:
            if size % _NOTE.size:
                raise ValueError("Unrecognized FL Studio note record layout")
            note_count = size // _NOTE.size
            total += note_count
            if current_pattern is None:
                orphans += note_count
            elif current_pattern in patterns:
                node = patterns[current_pattern]
                node["note_count"] += note_count
                for note_idx in range(note_count):
                    (
                        position,
                        flags,
                        rack_channel,
                        duration,
                        key,
                        group,
                        fine_pitch,
                        unused,
                        release,
                        midi_channel,
                        pan,
                        velocity,
                        mod_x,
                        mod_y,
                    ) = _NOTE.unpack_from(data, start + note_idx * _NOTE.size)
                    node["channel_note_counts"][rack_channel] += 1
                    if returned < max_notes:
                        node["notes"].append(
                            {
                                "channel_id": rack_channel,
                                "pitch": key,
                                "position_ticks": position,
                                "length_ticks": duration,
                                "velocity_raw": velocity,
                                "flags": flags,
                            }
                        )
                        returned += 1
        offset = end

    result = []
    for entry in patterns.values():
        entry["channel_note_counts"] = [
            {"channel_id": key, "note_count": value}
            for key, value in sorted(entry["channel_note_counts"].items())
        ]
        result.append(entry)
    return {
        "ppq": ppq,
        "pattern_count": len(patterns),
        "patterns": result,
        "patterns_over_limit": extra_patterns,
        "total_note_count": total,
        "unattributed_note_count": orphans,
        "notes_returned": returned,
        "notes_truncated": returned < total,
        "read_only": True,
        "limitations": (
            "Pattern-to-channel note associations only; no playlist clip, "
            "plugin, sample, or mixer ownership inferred."
        ),
    }
