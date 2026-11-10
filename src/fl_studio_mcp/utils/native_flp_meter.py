"""Safely patch the project-level meter in a copied native FLP scaffold.

FL Studio stores global project time-signature numerator/denominator in
single-byte events 0x11 and 0x12. Only modify an existing, unambiguous pair;
never invent event placement or rewrite other proprietary FLP events.

This backend verifies *offline binary semantics*, not real FL Studio runtime.
"""

from __future__ import annotations

from pathlib import Path

EVENT_METER_NUMERATOR = 17
EVENT_METER_DENOMINATOR = 18
SUPPORTED_DENOMINATORS = frozenset((1, 2, 4, 8, 16, 32, 64))


def _meter_offsets(data: bytes) -> tuple[int, int]:
    """Locate an existing, unique project-meter event pair in FLP data.

    Uses flpkit's validated binary event walker, which accounts for FLP's
    variable-length event framing and measured event-size exceptions. Reject
    absent or ambiguous events rather than mutating unrelated project bytes.
    """
    from flpkit import codec

    header, event_stream = codec.chunks(data)
    stream_start = 8 + len(header) + 8
    positions: dict[int, list[int]] = {
        EVENT_METER_NUMERATOR: [],
        EVENT_METER_DENOMINATOR: [],
    }
    for event_id, offset, size in codec.Stream(event_stream):
        if event_id in positions:
            if size != 1:
                raise ValueError("Native FLP meter event has unexpected byte length")
            positions[event_id].append(stream_start + offset)
    if any(len(offsets) != 1 for offsets in positions.values()):
        raise ValueError(
            "Native FLP template requires exactly one existing numerator "
            "and one existing denominator event"
        )
    return positions[EVENT_METER_NUMERATOR][0], positions[EVENT_METER_DENOMINATOR][0]


def read_native_meter(path: str | Path) -> tuple[int, int]:
    """Read the raw FLP project time signature without invoking FL Studio."""
    raw = Path(path).read_bytes()
    numerator_offset, denominator_offset = _meter_offsets(raw)
    numerator, denominator = raw[numerator_offset], raw[denominator_offset]
    if not 1 <= numerator <= 255 or denominator not in SUPPORTED_DENOMINATORS:
        raise ValueError("Native FLP project contains an invalid meter")
    return numerator, denominator


def set_native_meter(path: str | Path, numerator: int, denominator: int) -> None:
    """Patch only two validated meter bytes in an already-copied FLP file."""
    if (
        type(numerator) is not int
        or type(denominator) is not int
        or not 1 <= numerator <= 255
        or denominator not in SUPPORTED_DENOMINATORS
    ):
        raise ValueError("Unsupported native FLP time signature")
    output = Path(path)
    raw = bytearray(output.read_bytes())
    numerator_offset, denominator_offset = _meter_offsets(bytes(raw))
    raw[numerator_offset], raw[denominator_offset] = numerator, denominator
    output.write_bytes(raw)
    if read_native_meter(output) != (numerator, denominator):
        raise ValueError("Native FLP project meter did not match offline readback")
