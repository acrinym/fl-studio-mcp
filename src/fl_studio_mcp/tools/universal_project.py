"""DAW-agnostic source intelligence and explicit reconstruction bundles."""

from __future__ import annotations

import zipfile
from typing import TYPE_CHECKING

from fl_studio_mcp.utils.reconstruction_bundle import build_dawproject_bundle
from fl_studio_mcp.utils.universal_project import read_dawproject

if TYPE_CHECKING:
    from fastmcp import FastMCP


def register_universal_project_tools(mcp: FastMCP) -> None:
    @mcp.tool()
    def fl_inspect_universal_project(path: str) -> dict:
        """Read a DAWproject 1.0 ZIP into a bounded musical project graph.

        Includes tracks, clips, note events, routing, devices, automation and
        hashes of embedded resources; preserves unknowns as source references.
        Read-only: never extracts audio, loads plugins, or runs FL Studio.
        """
        try:
            return read_dawproject(path)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            return {"error": f"{type(exc).__name__}: {exc}", "read_only": True}

    @mcp.tool()
    def fl_build_reconstruction_bundle(path: str, output_dir: str) -> dict:
        """Create a NEW FL Studio reconstruction bundle from DAWproject 1.0.

        Explicitly writes a separate directory containing MIDI per source
        track, exact referenced media/state copies, musical graph and detailed
        fidelity receipt. Refuses overwrite. DOES NOT create an FLP, modify a
        DAW project, load plugins or control a running FL Studio instance.
        Unmapped clips/automation/routing are flagged, not faked as complete.
        """
        try:
            return build_dawproject_bundle(path, output_dir)
        except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile) as exc:
            return {"error": f"{type(exc).__name__}: {exc}", "source_modified": False}
