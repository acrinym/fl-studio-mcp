"""Read-only, bounded inspection of FL Studio native containers.

This is deliberately not a serializer: unknown FL Studio events and proprietary
plugin chunks must never be silently rewritten.
"""
from __future__ import annotations

import struct
import zipfile
from pathlib import Path

MAX_FILE_BYTES = 128 * 1024 * 1024
MAX_ZIP_MEMBERS = 2048
MAX_ZIP_TOTAL_BYTES = 512 * 1024 * 1024


def _fl_header(data: bytes) -> dict:
    """Validate FLhd and FLdt framing without interpreting undocumented events."""
    if len(data) < 22 or data[:4] != b"FLhd":
        raise ValueError("Not an FL Studio FLhd container")
    header_size = struct.unpack_from("<I", data, 4)[0]
    if header_size < 6 or header_size > 4096:
        raise ValueError("Invalid FLhd header size")
    data_offset = 8 + header_size
    if len(data) < data_offset + 8 or data[data_offset:data_offset + 4] != b"FLdt":
        raise ValueError("Missing FLdt chunk")
    declared = struct.unpack_from("<I", data, data_offset + 4)[0]
    remaining = len(data) - (data_offset + 8)
    if declared != remaining:
        raise ValueError("FLdt length mismatch; file may be truncated or malformed")
    return {
        "signature": "FLhd",
        "header_size": header_size,
        "format_code": struct.unpack_from("<H", data, 8)[0],
        "channel_count": struct.unpack_from("<H", data, 10)[0],
        "ppq": struct.unpack_from("<H", data, 12)[0],
        "event_data_bytes": declared,
        "size_bytes": len(data),
        "valid_framing": True,
        "unknown_events_preserved": "not decoded or modified",
    }


def inspect_fl_studio_file(path: str) -> dict:
    """Inspect FLP, FST and zipped projects without extraction or mutation."""
    file = Path(path).expanduser().resolve(strict=True)
    if not file.is_file():
        raise ValueError("Expected a regular file")
    if file.stat().st_size > MAX_FILE_BYTES and file.suffix.lower() != ".zip":
        raise ValueError("File exceeds bounded inspection limit")
    suffix = file.suffix.lower()
    if suffix in {".flp", ".fst"}:
        data = file.read_bytes()
        return {"path": str(file), "kind": suffix[1:], **_fl_header(data)}
    if suffix != ".zip":
        raise ValueError("Supported extensions: .flp, .fst, .zip")
    with zipfile.ZipFile(file) as archive:
        members = archive.infolist()
        if len(members) > MAX_ZIP_MEMBERS:
            raise ValueError("Too many ZIP members")
        if sum(m.file_size for m in members) > MAX_ZIP_TOTAL_BYTES:
            raise ValueError("ZIP uncompressed size exceeds inspection limit")
        projects = []
        for entry in members:
            if entry.is_dir() or not entry.filename.lower().endswith(".flp"):
                continue
            if entry.file_size > MAX_FILE_BYTES:
                raise ValueError("Embedded FLP exceeds inspection limit")
            with archive.open(entry) as stream:
                data = stream.read(MAX_FILE_BYTES + 1)
            if len(data) > MAX_FILE_BYTES:
                raise ValueError("Embedded FLP exceeds inspection limit")
            projects.append({"member": entry.filename, **_fl_header(data)})
        return {
            "path": str(file),
            "kind": "zipped_project",
            "member_count": len(members),
            "members": [
                {"name": item.filename, "bytes": item.file_size}
                for item in members
            ],
            "projects": projects,
            "read_only": True,
        }
