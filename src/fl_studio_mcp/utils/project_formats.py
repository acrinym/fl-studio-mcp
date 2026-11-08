"""Read-only, bounded inspection of FL Studio native containers.

This is deliberately not a serializer: unknown FL Studio events and proprietary
plugin chunks must never be silently rewritten.
"""

from __future__ import annotations

import struct
import zipfile
from pathlib import Path

from fl_studio_mcp.utils.channel_graph import inspect_channel_graph
from fl_studio_mcp.utils.fl_events import FORMAT_NAMES, inspect_event_stream
from fl_studio_mcp.utils.mixer_graph import MAX_MIXER_INSERT_RESULT, inspect_mixer_graph
from fl_studio_mcp.utils.pattern_graph import MAX_NOTE_RESULT, inspect_pattern_note_graph
from fl_studio_mcp.utils.playlist_graph import MAX_CLIP_RESULT, inspect_playlist_graph

MAX_FILE_BYTES = 128 * 1024 * 1024
MAX_ZIP_MEMBERS = 2048
MAX_ZIP_TOTAL_BYTES = 512 * 1024 * 1024


def _fl_header(
    data: bytes,
    *,
    include_note_graph: bool = False,
    max_notes: int = 1024,
    include_playlist_graph: bool = False,
    max_clips: int = 512,
    include_channel_graph: bool = False,
    include_mixer_graph: bool = False,
    max_mixer_inserts: int = 32,
) -> dict:
    """Validate FLhd/FLdt framing and optionally decode relationships."""
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
    if include_mixer_graph:
        if result["format_code"] in (0, 64):
            result["mixer_graph"] = inspect_mixer_graph(
                data[data_offset + 8 :],
                fl_version=result["event_summary"]["fl_version"],
                max_mixer_inserts=max_mixer_inserts,
            )
        else:
            result["mixer_graph_skipped"] = "Not a project or mixer insert-state container"
    if include_channel_graph:
        if result["format_code"] in (0, 32):
            result["channel_graph"] = inspect_channel_graph(
                data[data_offset + 8 :], fl_version=result["event_summary"]["fl_version"]
            )
        else:
            result["channel_graph_skipped"] = "Not a project or channel-state container"
    if include_note_graph:
        if result["format_code"] in (0, 16):
            result["note_graph"] = inspect_pattern_note_graph(
                data[data_offset + 8 :], ppq=result["ppq"], max_notes=max_notes
            )
        else:
            result["note_graph_skipped"] = "Not a project or score container"
    if include_playlist_graph:
        if result["format_code"] == 0:
            result["playlist_graph"] = inspect_playlist_graph(
                data[data_offset + 8 :],
                ppq=result["ppq"],
                max_clips=max_clips,
                fl_version=result["event_summary"]["fl_version"],
            )
        else:
            result["playlist_graph_skipped"] = "Not a project container"
    return result


def inspect_fl_studio_file(
    path: str,
    *,
    include_note_graph: bool = False,
    max_notes: int = 1024,
    include_playlist_graph: bool = False,
    max_clips: int = 512,
    include_channel_graph: bool = False,
    include_mixer_graph: bool = False,
    max_mixer_inserts: int = 32,
) -> dict:
    """Inspect FLP, FST and zipped projects without extraction or mutation."""
    if type(include_note_graph) is not bool:
        raise ValueError("include_note_graph must be a boolean")
    if type(max_notes) is not int or not 0 <= max_notes <= MAX_NOTE_RESULT:
        raise ValueError(f"max_notes must be an integer from 0 through {MAX_NOTE_RESULT}")
    if type(include_playlist_graph) is not bool:
        raise ValueError("include_playlist_graph must be a boolean")
    if type(include_channel_graph) is not bool:
        raise ValueError("include_channel_graph must be a boolean")
    if type(include_mixer_graph) is not bool:
        raise ValueError("include_mixer_graph must be a boolean")
    if type(max_mixer_inserts) is not int or not 0 <= max_mixer_inserts <= MAX_MIXER_INSERT_RESULT:
        raise ValueError(
            f"max_mixer_inserts must be an integer from 0 through {MAX_MIXER_INSERT_RESULT}"
        )
    if type(max_clips) is not int or not 0 <= max_clips <= MAX_CLIP_RESULT:
        raise ValueError(f"max_clips must be an integer from 0 through {MAX_CLIP_RESULT}")
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
            **_fl_header(
                data,
                include_note_graph=include_note_graph,
                max_notes=max_notes,
                include_playlist_graph=include_playlist_graph,
                max_clips=max_clips,
                include_channel_graph=include_channel_graph,
                include_mixer_graph=include_mixer_graph,
                max_mixer_inserts=max_mixer_inserts,
            ),
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
                graph_selected = (
                    include_note_graph
                    or include_playlist_graph
                    or include_channel_graph
                    or include_mixer_graph
                ) and len(projects) < 4
                project = {
                    "member": entry.filename,
                    **_fl_header(
                        data,
                        include_note_graph=graph_selected and include_note_graph,
                        max_notes=max_notes,
                        include_playlist_graph=graph_selected and include_playlist_graph,
                        max_clips=max_clips,
                        include_channel_graph=graph_selected and include_channel_graph,
                        include_mixer_graph=graph_selected and include_mixer_graph,
                        max_mixer_inserts=max_mixer_inserts,
                    ),
                }
                if include_note_graph and not graph_selected:
                    project["note_graph_skipped"] = "Graph inspection limited to first 4 members"
                if include_playlist_graph and not graph_selected:
                    project["playlist_graph_skipped"] = (
                        "Graph inspection limited to first 4 members"
                    )
                if include_channel_graph and not graph_selected:
                    project["channel_graph_skipped"] = "Graph inspection limited to first 4 members"
                if include_mixer_graph and not graph_selected:
                    project["mixer_graph_skipped"] = "Graph inspection limited to first 4 members"
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
