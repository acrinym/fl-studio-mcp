"""Bounded, read-only FL Studio playlist-to-pattern/channel relationships.

Factual layout references: public FL Studio file-format reverse-engineering
documentation (PyFLP arrangement schema); no GPL code is imported or copied.
Playlist event 233 uses 32-byte historical or 60-byte modern clip records.
Unknown layouts are counted, not guessed. No source files are modified.
"""

from __future__ import annotations

import re
import struct
from collections import Counter

from fl_studio_mcp.utils.channel_graph import inspect_channel_graph
from fl_studio_mcp.utils.fl_events import MAX_EVENTS, _varint_length
from fl_studio_mcp.utils.pattern_graph import inspect_pattern_note_graph

MAX_CLIP_RESULT = 2048
MAX_PLAYLIST_BLOCK_BYTES = 8 * 1024 * 1024
_PLAYLIST_ID = 233
_ARRANGEMENT_NEW = 99
_TRACK_DATA = 238
_PATTERN_BASE = 20480


def _format_version_major(version: str | None) -> int | None:
    """Extract major version for format selection; never assume on bad metadata."""
    if version is None:
        return None
    match = re.match(r"^(\d+)(?:\.|$)", version)
    return int(match.group(1)) if match else None


def _valid_stride(data: bytes, stride: int) -> bool:
    """Accept a record layout only if every record has known framing markers."""
    if not data or len(data) % stride:
        return False
    for offset in range(0, len(data), stride):
        base = struct.unpack_from("<H", data, offset + 4)[0]
        track_reverse = struct.unpack_from("<H", data, offset + 12)[0]
        if base != _PATTERN_BASE or track_reverse > 499:
            return False
        # Start/end offsets may use 0xFFFFFFFF (NaN) as an unset sentinel
        # in real FL Studio projects; their float values are not decoded here.
    return True


def _choose_stride(data: bytes, major_version: int | None) -> int | None:
    """Select documented 32/60-byte format without silently guessing on ties."""
    valid = [stride for stride in (32, 60) if _valid_stride(data, stride)]
    if len(valid) == 1:
        return valid[0]
    if len(valid) == 2 and major_version is not None:
        return 60 if major_version >= 21 else 32
    return None


def inspect_playlist_graph(
    data: bytes, *, ppq: int, max_clips: int = 512, fl_version: str | None = None
) -> dict:
    """Link playlist clips to patterns and their decoded rack-channel note IDs.

    Arrangement and track identities are only inferred from documented events.
    Audio/automation channel clips stay distinct from pattern clips; no plugin
    name, mixer route, or unknown event is invented.
    """
    if type(max_clips) is not int or not 0 <= max_clips <= MAX_CLIP_RESULT:
        raise ValueError(f"max_clips must be an integer from 0 through {MAX_CLIP_RESULT}")

    offset = 0
    event_count = 0
    arrangements: list[dict] = []
    current: dict | None = None

    def ensure_arrangement() -> dict:
        """Create a legacy unnamed arrangement only when a playlist event needs it."""
        nonlocal current
        if current is None:
            current = {"arrangement_id": None, "playlist_payloads": [], "track_count": 0}
            arrangements.append(current)
        return current

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
        if event_id == _ARRANGEMENT_NEW:
            current = {
                "arrangement_id": struct.unpack_from("<H", data, start)[0],
                "playlist_payloads": [],
                "track_count": 0,
            }
            arrangements.append(current)
        elif event_id == _PLAYLIST_ID:
            ensure_arrangement()["playlist_payloads"].append(data[start:end])
        elif event_id == _TRACK_DATA:
            if current is not None:
                current["track_count"] += 1
        offset = end

    note_graph = inspect_pattern_note_graph(data, ppq=ppq, max_notes=0)
    patterns = {p["pattern_id"]: p for p in note_graph["patterns"]}
    channel_graph = inspect_channel_graph(data, fl_version=fl_version)
    channel_map = {ch["channel_id"]: ch for ch in channel_graph["channels"]}
    notes_complete = (
        note_graph["patterns_over_limit"] == 0
        and note_graph["note_blocks_unrecognized_layout"] == 0
    )
    output: list[dict] = []
    accepted_clips = 0
    returned = 0
    unknown_chunks = 0
    formats: Counter[int] = Counter()
    major_version = _format_version_major(fl_version)
    for arrangement in arrangements:
        track_count = arrangement["track_count"]
        track_index_max = track_count - 1 if track_count in (199, 500) else None
        result = {
            "arrangement_id": arrangement["arrangement_id"],
            "track_data_events": track_count,
            "clip_count": 0,
            "clips": [],
            "unrecognized_playlist_blocks": 0,
        }
        for payload in arrangement["playlist_payloads"]:
            if not payload:
                continue
            if len(payload) > MAX_PLAYLIST_BLOCK_BYTES:
                unknown_chunks += 1
                result["unrecognized_playlist_blocks"] += 1
                continue
            stride = _choose_stride(payload, major_version)
            if stride is None:
                unknown_chunks += 1
                result["unrecognized_playlist_blocks"] += 1
                continue
            formats[stride] += 1
            for pos in range(0, len(payload), stride):
                (position, base, item_index, length, reverse_track, group) = struct.unpack_from(
                    "<IHHIHH", payload, pos
                )
                result["clip_count"] += 1
                accepted_clips += 1
                if returned >= max_clips:
                    continue
                clip = {
                    "kind": "pattern" if item_index > base else "channel",
                    "playlist_track_number": (
                        track_index_max - reverse_track + 1
                        if track_index_max is not None and reverse_track <= track_index_max
                        else None
                    ),
                    "playlist_track_reversed_index": reverse_track,
                    "position_ticks": position,
                    "length_ticks": length,
                    "group": group,
                }
                if item_index > base:
                    pattern_id = item_index - base
                    pattern = patterns.get(pattern_id)
                    clip["pattern_id"] = pattern_id
                    clip["pattern_found"] = pattern is not None
                    clip["pattern_note_count"] = pattern["note_count"] if pattern else None
                    clip["pattern_channel_note_counts"] = (
                        pattern["channel_note_counts"] if pattern else []
                    )
                    clip["note_links_complete"] = notes_complete
                    clip["pattern_channel_details"] = [
                        {
                            **ref,
                            "channel_found": ref["channel_id"] in channel_map,
                            "display_name": (
                                channel_map[ref["channel_id"]]["display_name"]
                                if ref["channel_id"] in channel_map
                                else None
                            ),
                            "internal_plugin_name": (
                                channel_map[ref["channel_id"]]["internal_plugin_name"]
                                if ref["channel_id"] in channel_map
                                else None
                            ),
                            "plugin_identity": (
                                channel_map[ref["channel_id"]]["plugin_identity"]
                                if ref["channel_id"] in channel_map
                                else "unresolved"
                            ),
                            "mixer_insert_index": (
                                channel_map[ref["channel_id"]]["mixer_insert_index"]
                                if ref["channel_id"] in channel_map
                                else None
                            ),
                        }
                        for ref in clip["pattern_channel_note_counts"]
                    ]
                else:
                    # This is an audio/automation channel reference, not a pattern.
                    clip["channel_id"] = item_index
                    channel = channel_map.get(item_index)
                    clip["channel_found"] = channel is not None
                    clip["channel_display_name"] = channel["display_name"] if channel else None
                result["clips"].append(clip)
                returned += 1
        output.append(result)

    return {
        "ppq": ppq,
        "arrangement_count": len(output),
        "arrangements": output,
        "total_clips": accepted_clips,
        "clips_returned": returned,
        "clips_truncated": returned < accepted_clips,
        "unrecognized_playlist_blocks": unknown_chunks,
        "playlist_record_formats": [
            {"record_bytes": stride, "blocks": count} for stride, count in sorted(formats.items())
        ],
        "pattern_note_links_incomplete": not notes_complete,
        "channel_links_incomplete": channel_graph["channels_truncated"]
        or any(
            item["channel_id"] not in channel_map
            for pattern in patterns.values()
            for item in pattern["channel_note_counts"]
        ),
        "read_only": True,
        "limitations": (
            "Track numbers require a known 199/500-track schema; pattern channels "
            "are encoded rack IDs; displayed names may be renamed. "
            "Fruity Wrapper does not identify a VST product. "
            "Unknown clip layouts are not decoded."
        ),
    }
