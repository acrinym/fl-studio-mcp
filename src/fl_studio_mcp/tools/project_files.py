"""Read-only native FL Studio project and preset inspection."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fl_studio_mcp.utils.project_formats import inspect_fl_studio_file

if TYPE_CHECKING:
    from fastmcp import FastMCP


def register_project_file_tools(mcp: FastMCP) -> None:
    @mcp.tool()
    def fl_inspect_native_file(path: str) -> dict:
        """Inspect an FLP, FST or zipped FL Studio project, without editing it.

        Returns validated framing, format classification, structural FL events,
        and ZIP members; proprietary plugin parameter data stays opaque.
        This tool never loads a plugin, changes a preset or modifies a project.
        """
        try:
            return inspect_fl_studio_file(path)
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc}", "read_only": True}
