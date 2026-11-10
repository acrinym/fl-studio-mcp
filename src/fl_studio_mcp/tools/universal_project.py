"""DAW-agnostic source intelligence and explicit reconstruction bundles."""

from __future__ import annotations

import zipfile
import zlib
from typing import TYPE_CHECKING

from fl_studio_mcp.utils.native_flp_template import build_native_flp_from_template
from fl_studio_mcp.utils.reconstruction_bundle import build_dawproject_bundle
from fl_studio_mcp.utils.universal_project import read_dawproject

if TYPE_CHECKING:
    from fastmcp import FastMCP


_EXPECTED_ARCHIVE_ERRORS = (
    OSError,
    ValueError,
    KeyError,
    TypeError,
    EOFError,
    NotImplementedError,
    RuntimeError,  # encrypted members raise RuntimeError in zipfile
    zipfile.BadZipFile,
    zipfile.LargeZipFile,
    zlib.error,
)


def register_universal_project_tools(mcp: FastMCP) -> None:
    """Register universal source inspection, reconstruction and template-FLP tools."""

    @mcp.tool()
    def fl_inspect_universal_project(path: str) -> dict:
        """Read a DAWproject 1.0 ZIP into a bounded musical project graph.

        Includes tracks, clips, note events, routing, devices, automation and
        hashes of embedded resources; preserves unknowns as source references.
        Read-only: never extracts audio, loads plugins, or runs FL Studio.
        """
        try:
            return read_dawproject(path)
        except _EXPECTED_ARCHIVE_ERRORS as exc:
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
        except _EXPECTED_ARCHIVE_ERRORS as exc:
            return {"error": f"{type(exc).__name__}: {exc}", "source_modified": False}

    @mcp.tool()
    def fl_build_native_flp_from_template(path: str, template_flp: str, output_dir: str) -> dict:
        """Create a NEW native FLP copy from DAWproject using a blank FLP scaffold.

        Uses optional MIT flpkit 0.9.0. This is an offline, limited
        note/pattern/tempo reconstruction, NOT an FL Studio app qualification
        and never complete conversion. Source/template files remain unchanged.
        Refuses an existing output directory and source content it cannot map.
        No installed FL Studio, plugin or running session is touched.
        """
        try:
            return build_native_flp_from_template(path, template_flp, output_dir)
        except Exception as exc:  # native optional backend raises format-specific errors
            return {
                "error": f"{type(exc).__name__}: {exc}",
                "native_flp_created": False,
                "fl_studio_open_verified": False,
                "source_modified": False,
                "template_modified": False,
            }
