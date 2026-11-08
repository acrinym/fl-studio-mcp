"""Read-only channel identity inventory for FL Studio FLP/FST event streams.

Uses public FL Studio channel/plugin event IDs as documented by PyFLP, without
importing or copying PyFLP implementation. A channel display label is NOT
proof of a third-party VST's actual identity. Vendor chunks remain opaque.
"""

from __future__ import annotations

import re
import struct

from fl_studio_mcp.utils.fl_events import MAX_EVENTS, _varint_length

MAX_CHANNEL_RESULT = 256
MAX_NAME_CHARS = 96
CHANNEL_TYPES = {
    0: "sampler",
    2: "native",
    3: "layer",
    4: "instrument",
    5: "automation",
}


def _version_is_unicode(version: str | None) -> bool:
    """Use FL Studio's UTF-16LE text convention from version 11.5 onward."""
    if not version:
        return False
    match = re.match(r"^(\d+)\.(\d+)", version)
    return bool(match and tuple(map(int, match.groups())) >= (11, 5))


def _label(payload: bytes, *, unicode: bool) -> str | None:
    """Decode a bounded plugin/channel label, never arbitrary binary payloads."""
    raw = payload[: MAX_NAME_CHARS * 2 + 2]
    # If version metadata is unavailable, avoid mistaking visibly UTF-16LE
    # labels for one-character ASCII names (e.g. b"K\\0i\\0...").
    unicode = unicode or (
        len(raw) >= 4
        and len(raw) % 2 == 0
        and raw.endswith(b"\x00\x00")
        and raw[1] == 0
        and raw[3] == 0
    )
    if unicode:
        if len(raw) % 2:
            return None
        text = raw.decode("utf-16-le", errors="replace").split("\x00", 1)[0]
    else:
        text = raw.decode("utf-8", errors="replace").split("\x00", 1)[0]
    text = text.strip()
    if not text:
        return None
    if any(not ch.isprintable() for ch in text):
        return None
    return text[:MAX_NAME_CHARS]


def inspect_channel_graph(data: bytes, *, fl_version: str | None) -> dict:
    """Associate channel IDs with display labels, native plugins and mixer inserts.

    Sources: channel-new (64), type (21), name/internal-name (203/201),
    routed-to (22 signed byte), enabled (0), wrapper (212), state (213),
    and sample-reference presence (196). Never return sample paths or states.
    The channel region ends at the first arrangement (99).
    """
    # Some older projects have no FLVersion event; ASCII fallback avoids
    # inventing labels from unknown UTF-16/UTF-8 encoding.
    unicode = _version_is_unicode(fl_version)
    offset = 0
    events = 0
    current: dict | None = None
    channels: dict[int, dict] = {}
    ids_seen: set[int] = set()
    duplicates = 0
    ignored_over_limit = 0
    channel_section_closed = False
    while offset < len(data):
        if events >= MAX_EVENTS:
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
        events += 1
        if event_id == 99:
            channel_section_closed = True
            current = None
        elif event_id == 64 and not channel_section_closed:
            channel_id = struct.unpack_from("<H", data, start)[0]
            if channel_id in ids_seen:
                duplicates += 1
                current = None
            else:
                ids_seen.add(channel_id)
                if len(channels) >= MAX_CHANNEL_RESULT:
                    ignored_over_limit += 1
                    current = None
                else:
                    current = {
                        "channel_id": channel_id,
                        "channel_type_code": None,
                        "channel_type": None,
                        "display_name": None,
                        "internal_plugin_name": None,
                        "plugin_identity": "unresolved",
                        "mixer_insert_index": None,
                        "enabled": None,
                        "has_plugin_wrapper": False,
                        "opaque_plugin_state_bytes": 0,
                        "has_sample_reference": False,
                    }
                    channels[channel_id] = current
        elif current is not None and not channel_section_closed:
            if event_id == 21 and current["channel_type_code"] is None:
                code = data[start]
                current["channel_type_code"] = code
                current["channel_type"] = CHANNEL_TYPES.get(code, f"unknown ({code})")
            elif event_id == 0 and current["enabled"] is None:
                current["enabled"] = bool(data[start])
            elif event_id == 22 and current["mixer_insert_index"] is None:
                current["mixer_insert_index"] = struct.unpack_from("<b", data, start)[0]
            elif event_id in (201, 203):
                field = "internal_plugin_name" if event_id == 201 else "display_name"
                if current[field] is None:
                    current[field] = _label(data[start:end], unicode=unicode)
            elif event_id == 212:
                current["has_plugin_wrapper"] = True
            elif event_id == 213:
                current["opaque_plugin_state_bytes"] += size
            elif event_id == 196:
                current["has_sample_reference"] = True
        offset = end

    output = []
    for item in channels.values():
        name = item["internal_plugin_name"]
        kind = item["channel_type"]
        if name and name.lower() == "fruity wrapper":
            identity = "third_party_wrapper_unresolved"
        elif name:
            identity = "native_plugin_internal_name"
        elif kind == "sampler":
            identity = "fl_studio_sampler"
        elif kind in ("layer", "automation"):
            identity = "not_applicable"
        else:
            identity = "unresolved"
        item["plugin_identity"] = identity
        output.append(item)
    return {
        "channels": output,
        "channel_count": len(output),
        "channel_ids_over_limit": ignored_over_limit,
        "duplicate_channel_ids": duplicates,
        "channels_truncated": bool(ignored_over_limit),
        "read_only": True,
        "notes": (
            "Display labels may be user-renamed; internal names identify stock "
            "generators or the generic Fruity Wrapper, NOT a VST vendor/product. "
            "Mixer insert -1 denotes current; 0 denotes Master. "
            "Sample paths and vendor state are never returned."
        ),
    }
