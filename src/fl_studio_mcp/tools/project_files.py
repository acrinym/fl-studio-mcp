"""Read-only native FL Studio project and preset inspection."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fl_studio_mcp.utils.project_formats import inspect_fl_studio_file

if TYPE_CHECKING:
    from fastmcp import FastMCP


def register_project_file_tools(mcp: FastMCP) -> None:
    @mcp.tool()
    def fl_inspect_native_file(
        path: str,
        include_note_graph: bool = False,
        max_notes: int = 1024,
        include_playlist_graph: bool = False,
        max_clips: int = 512,
        include_channel_graph: bool = False,
    ) -> dict:
        """Inspect an FLP, FST or zipped FL Studio project, without editing it.

        Returns structural FL events and ZIP inventory. Set include_note_graph
        to inspect pattern IDs, channel-note relationships and note positions,
        capped by max_notes. Set include_playlist_graph to map playlist clips
        to pattern IDs and the rack channel IDs used by their decoded notes,
        capped by max_clips. Set include_channel_graph to inspect named
        channels, native plugin identity hints, and their mixer insert routing.
        Proprietary plugin state remains opaque.
        This tool never loads a plugin, changes a preset or modifies a project.
        """
        try:
            return inspect_fl_studio_file(
                path,
                include_note_graph=include_note_graph,
                max_notes=max_notes,
                include_playlist_graph=include_playlist_graph,
                max_clips=max_clips,
                include_channel_graph=include_channel_graph,
            )
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc}", "read_only": True}
