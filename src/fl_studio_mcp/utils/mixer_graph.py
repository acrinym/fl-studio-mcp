"""Read-only FL Studio mixer section inventory.

References the public descriptions of event IDs and 12-byte parameter records.
No code is imported from GPL PyFLP, and no opaque plugin state is decoded.

The output uses *raw mixer record indices* instead of asserting a stable
Master/current/insert-number mapping across FL Studio versions.
"""

from __future__ import annotations

import struct

from fl_studio_mcp.utils.channel_graph import _label, _version_is_unicode
from fl_studio_mcp.utils.fl_events import MAX_EVENTS, _varint_length

MAX_MIXER_INSERT_RESULT = 128
MAX_MIXER_EVENTS = 256
MAX_ROUTING_MASK = 256
MAX_PARAMS_BYTES = 8 * 1024 * 1024
MAX_FX_SLOT_ITEMS = 32
_PARAM = struct.Struct("<4sBBHi")

_MIXER_FLAGS = 236
_MIXER_ROUTING = 235
_MIXER_PARAMETERS = 225
_MIXER_INSERT_NAME = 204
_MIXER_SLOT_INDEX = 98
_MIXER_INPUT = 154
_MIXER_OUTPUT = 147
_PLUGIN_INTERNAL_NAME = 201
_PLUGIN_DISPLAY_NAME = 203
_PLUGIN_WRAPPER = 212
_PLUGIN_DATA = 213


def _new_insert(index: int) -> dict:
    return {
        "mixer_record_index": index,
        "display_name": None,
        "flags_raw": None,
        "enabled": None,
        "effects_enabled": None,
        "routing_mask_set_indices": [],
        "routing_mask_unrecognized": False,
        "input_raw": None,
        "output_raw": None,
        "declared_fx_slot_count": 0,
        "occupied_fx_slots": [],
        "volume_raw": None,
        "pan_raw": None,
        "stereo_separation_raw": None,
        "send_parameter_target_indices": [],
    }


def inspect_mixer_graph(
    data: bytes, *, fl_version: str | None = None, max_mixer_inserts: int = 32
) -> dict:
    """Inventory mixer records, their bounded FX slots, routing masks and params.

    Slot payloads and plugin vendor state never leave this function. Numerical
    volume/pan and route values are deliberately marked raw, not normalized
    controls; their semantics may vary by FL Studio version.
    """
    if type(max_mixer_inserts) is not int or not 0 <= max_mixer_inserts <= MAX_MIXER_INSERT_RESULT:
        raise ValueError(
            f"max_mixer_inserts must be an integer from 0 through {MAX_MIXER_INSERT_RESULT}"
        )
    inserts: list[dict] = []
    current: dict | None = None
    slot: dict | None = None
    offset = 0
    events = 0
    params_blocks: list[bytes] = []
    unknown_flags = 0
    unicode = _version_is_unicode(fl_version)
    extra_mixer_events = 0
    unrecognized_params = 0

    while offset < len(data):
        if events >= MAX_EVENTS:
            raise ValueError("FL Studio event count exceeds inspection limit")
        eid = data[offset]
        offset += 1
        if eid < 64:
            n = 1
        elif eid < 128:
            n = 2
        elif eid < 192:
            n = 4
        else:
            n, offset = _varint_length(data, offset)
        if n > len(data) - offset:
            raise ValueError("Truncated FL Studio event value")
        start, end = offset, offset + n
        events += 1

        if eid == _MIXER_FLAGS:
            slot = None
            if len(inserts) >= MAX_MIXER_EVENTS:
                extra_mixer_events += 1
                current = None
                offset = end
                continue
            current = _new_insert(len(inserts))
            inserts.append(current)
            if n >= 8:
                flags = struct.unpack_from("<I", data, start + 4)[0]
                current["flags_raw"] = flags
                current["enabled"] = bool(flags & 8)
                current["effects_enabled"] = bool(flags & 4)
            else:
                unknown_flags += 1
        elif eid == _MIXER_PARAMETERS:
            current = None
            slot = None
            if n <= MAX_PARAMS_BYTES and n % _PARAM.size == 0:
                params_blocks.append(data[start:end])
            else:
                unrecognized_params += 1
        elif current is not None:
            if eid == _MIXER_INSERT_NAME:
                slot = None
                if current["display_name"] is None:
                    current["display_name"] = _label(data[start:end], unicode=unicode)
            elif eid == _MIXER_ROUTING:
                slot = None
                if n <= MAX_ROUTING_MASK:
                    current["routing_mask_set_indices"] = [
                        i for i, state in enumerate(data[start:end]) if state
                    ]
                else:
                    current["routing_mask_unrecognized"] = True
            elif eid == _MIXER_INPUT:
                slot = None
                current["input_raw"] = struct.unpack_from("<i", data, start)[0]
            elif eid == _MIXER_OUTPUT:
                slot = None
                current["output_raw"] = struct.unpack_from("<i", data, start)[0]
            elif eid == _MIXER_SLOT_INDEX:
                current["declared_fx_slot_count"] += 1
                index = struct.unpack_from("<H", data, start)[0]
                slot = {
                    "slot_index": index,
                    "internal_plugin_name": None,
                    "display_name": None,
                    "has_plugin_wrapper": False,
                    "opaque_plugin_data_bytes": 0,
                }
                if len(current["occupied_fx_slots"]) < MAX_FX_SLOT_ITEMS:
                    current["occupied_fx_slots"].append(slot)
                else:
                    slot = None
            elif slot is not None:
                if eid == _PLUGIN_INTERNAL_NAME and slot["internal_plugin_name"] is None:
                    slot["internal_plugin_name"] = _label(data[start:end], unicode=unicode)
                elif eid == _PLUGIN_DISPLAY_NAME and slot["display_name"] is None:
                    slot["display_name"] = _label(data[start:end], unicode=unicode)
                elif eid == _PLUGIN_WRAPPER:
                    slot["has_plugin_wrapper"] = True
                elif eid == _PLUGIN_DATA:
                    slot["opaque_plugin_data_bytes"] += n
        offset = end

    # Mixer parameters often occur after every insert block. Never conflate
    # these with channel mixer routing bytes, or normalize undocumented values.
    for block in params_blocks:
        for off in range(0, len(block), _PARAM.size):
            _, param_id, _, channel_data, value = _PARAM.unpack_from(block, off)
            index = (channel_data >> 6) & 0x7F
            slot_index = channel_data & 0x3F
            if index >= len(inserts):
                unrecognized_params += 1
                continue
            item = inserts[index]
            if param_id == 192:
                item["volume_raw"] = value
            elif param_id == 193:
                item["pan_raw"] = value
            elif param_id == 194:
                item["stereo_separation_raw"] = value
            elif 64 <= param_id < 192:
                if (param_id - 64) not in item["send_parameter_target_indices"]:
                    item["send_parameter_target_indices"].append(param_id - 64)
            elif param_id in (0, 1):
                # Only annotate an explicitly occupied plugin slot.
                for fx in item["occupied_fx_slots"]:
                    if fx["slot_index"] == slot_index:
                        fx["enabled_raw" if param_id == 0 else "mix_raw"] = value
                        break

    total_occupied = 0
    for insert in inserts:
        insert["occupied_fx_slots"] = [
            fx
            for fx in insert["occupied_fx_slots"]
            if fx["internal_plugin_name"]
            or fx["display_name"]
            or fx["has_plugin_wrapper"]
            or fx["opaque_plugin_data_bytes"]
        ]
        total_occupied += len(insert["occupied_fx_slots"])
        insert["send_parameter_target_indices"].sort()

    return {
        "mixer_record_count": len(inserts),
        "mixer_records_over_limit": extra_mixer_events,
        "mixer_records": inserts[:max_mixer_inserts],
        "mixer_records_returned": min(len(inserts), max_mixer_inserts),
        "mixer_records_truncated": len(inserts) > max_mixer_inserts or bool(extra_mixer_events),
        "occupied_effect_slots_in_all_records": total_occupied,
        "mixer_records_with_effects": [
            item["mixer_record_index"] for item in inserts if item["occupied_fx_slots"]
        ],
        "mixer_records_with_names": [
            item["mixer_record_index"] for item in inserts if item["display_name"]
        ],
        "unknown_mixer_flag_blocks": unknown_flags,
        "unrecognized_mixer_parameter_records": unrecognized_params,
        "read_only": True,
        "limitations": (
            "Mixer record indices are raw event-order indices, not yet verified "
            "Master/current/visible mixer insert numbers. Routing masks and "
            "send targets are raw structural hints, not proven active audio paths. "
            "FX wrapper identifiers do not prove third-party plugin products."
        ),
    }
