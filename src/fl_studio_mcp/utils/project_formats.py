"""Read-only, bounded inspection of FL Studio native containers.

This is deliberately not a serializer: unknown FL Studio events and proprietary
plugin chunks must never be silently rewritten.
"""

from __future__ import annotations

import struct
import zipfile
from pathlib import Path

from fl_studio_mcp.utils.fl_events import FORMAT_NAMES, inspect_event_stream
from fl_studio_mcp.utils.pattern_graph import inspect_pattern_note_graph

MAX_FILE_BYTES = 128 * 1024 * 1024
MAX_ZIP_MEMBERS = 2048
MAX_ZIP_TOTAL_BYTES = 512 * 1024 * 1024


def _fl_header(data: bytes, *, include_note_graph: bool = False, max_notes: int = 1024) -> dict:
    """Validate FLhd/FLdt framing and optionally decode pattern-note relationships."""
    if len(data) < 22 or data[:4] != b"FLhd":
        raise ValueError("Not an FL Studio FLhd container")
    header_size = struct.unpack_from("<I", data, 4)[0]
    if header_size < 6 or header_size > 4096:
        raise ValueError("Invalid FLhd header size")
    data_offset = 8 + header_size
    if len(data) < data_offset + 8 or data[data_offset : data_offset + 4] != b"FLdt":
        raise ValueError("Missing FLdt chunk")
    declared = struct.unpack_from("<I", data, data_offset + 4)[0]
    remaining = len(data) - (data_offset + 8)
    if declared != remaining:
        raise ValueError("FLdt length mismatch; file may be truncated or malformed")
    result = {
        "signature": "FLhd",
        "header_size": header_size,
        "format_code": struct.unpack_from("<H", data, 8)[0],
        "format_name": FORMAT_NAMES.get(struct.unpack_from("<H", data, 8)[0], "unknown"),
        "channel_count": struct.unpack_from("<H", data, 10)[0],
        "ppq": struct.unpack_from("<H", data, 12)[0],
        "event_data_bytes": declared,
        "size_bytes": len(data),
        "valid_framing": True,
        "unknown_events_preserved": "no file or opaque plugin bytes modified",
        "event_summary": inspect_event_stream(
            data[data_offset + 8 :],
            format_code=struct.unpack_from("<H", data, 8)[0],
        ),
    }
    if include_note_graph and result["format_code"] in (0, 16):
        result["note_graph"] = inspect_pattern_note_graph(
            data[data_offset + 8 :], ppq=result["ppq"], max_notes=max_notes
        )
    return result


def inspect_fl_studio_file(
    path: str, *, include_note_graph: bool = False, max_notes: int = 1024
) -> dict:
    """Inspect FLP, FST and zipped projects without extraction or mutation."""
    if type(include_note_graph) is not bool:
        raise ValueError("include_note_graph must be a boolean")
    if type(max_notes) is not int or not 0 <= max_notes <= 4096:
        raise ValueError("max_notes must be an integer from 0 through 4096")
    file = Path(path).expanduser().resolve(strict=True)
    if not file.is_file():
        raise ValueError("Expected a regular file")
    if file.stat().st_size > MAX_FILE_BYTES and file.suffix.lower() != ".zip":
        raise ValueError("File exceeds bounded inspection limit")
    suffix = file.suffix.lower()
    if suffix in {".flp", ".fst"}:
        data = file.read_bytes()
        return {
            "path": str(file),
            "kind": suffix[1:],
            **_fl_header(data, include_note_graph=include_note_graph, max_notes=max_notes),
        }
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
                projects.append(
                    {
                        "member": entry.filename,
                        "error": "Embedded FLP exceeds inspection limit",
                    }
                )
                continue
            try:
                with archive.open(entry) as stream:
                    data = stream.read(MAX_FILE_BYTES + 1)
                if len(data) > MAX_FILE_BYTES:
                    raise ValueError("Embedded FLP exceeds inspection limit")
                graph_selected = include_note_graph and len(projects) < 4
                project = {
                    "member": entry.filename,
                    **_fl_header(data, include_note_graph=graph_selected, max_notes=max_notes),
                }
                if include_note_graph and not graph_selected:
                    project["note_graph_skipped"] = "Graph inspection limited to first 4 members"
                projects.append(project)
            except (ValueError, zipfile.BadZipFile, EOFError, OSError) as exc:
                # A corrupt member must not hide other healthy projects.
                projects.append({"member": entry.filename, "error": f"{type(exc).__name__}: {exc}"})
        if not projects:
            raise ValueError("ZIP contains no FL Studio .flp project")
        return {
            "path": str(file),
            "kind": "zipped_project",
            "member_count": len(members),
            "members": [{"name": item.filename, "bytes": item.file_size} for item in members[:100]],
            "members_truncated": len(members) > 100,
            "projects": projects,
            "read_only": True,
        }
