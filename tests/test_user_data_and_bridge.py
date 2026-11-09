"""Regressions for upstream user-data folders and shared MIDI transactions.

FL Studio-side path functions are loaded from source without starting the DAW;
real FL Studio embedded-Python access is intentionally not simulated.
"""

from __future__ import annotations

import ast
import json
import os
import sys
import threading
import time
import types
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fl_studio_mcp.tools import piano_roll
from fl_studio_mcp.utils import midi_connection

ROOT = Path(__file__).resolve().parents[1]


def _load_fl_path_function(relpath: str, name: str):
    """Load one pure path function without importing FL Studio-only modules."""
    path = ROOT / relpath
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    global_vars = {"os": os, "sys": sys, "Path": Path}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), str(path), "exec"), global_vars)
    return global_vars[name]


def test_userdata_root_is_shared_between_mcp_and_fl_scripts(monkeypatch, tmp_path):
    root = tmp_path / "Custom Image-Line"
    monkeypatch.delenv("FL_STUDIO_MCP_SETTINGS_DIR", raising=False)
    monkeypatch.setenv("FL_STUDIO_USER_DATA_DIR", str(root))
    settings = root / "FL Studio" / "Settings"
    assert midi_connection.get_fl_settings_base() == settings
    assert midi_connection._get_fl_hardware_dir() == settings / "Hardware" / "FLStudioMCP"
    assert piano_roll._get_fl_scripts_dir() == settings / "Piano roll scripts"

    controller = _load_fl_path_function("fl_controller/device_FLStudioMCP.py", "_get_script_dir")
    script = _load_fl_path_function("scripts/ComposeWithLLM.pyscript", "get_script_dir")
    assert controller() == settings / "Hardware" / "FLStudioMCP"
    assert Path(script()) == settings / "Piano roll scripts"


def test_legacy_settings_override_keeps_priority(monkeypatch, tmp_path):
    monkeypatch.setenv("FL_STUDIO_USER_DATA_DIR", str(tmp_path / "user-data"))
    monkeypatch.setenv("FL_STUDIO_MCP_SETTINGS_DIR", str(tmp_path / "existing-settings"))
    assert midi_connection.get_fl_settings_base() == tmp_path / "existing-settings"


def test_controller_finds_actual_installed_path_when_no_env(monkeypatch, tmp_path):
    monkeypatch.delenv("FL_STUDIO_USER_DATA_DIR", raising=False)
    hardware = tmp_path / "FL Studio" / "Settings" / "Hardware" / "FLStudioMCP"
    hardware.mkdir(parents=True)
    (hardware / "device_FLStudioMCP.py").write_text("# installed\n", encoding="utf-8")
    monkeypatch.setattr(sys, "path", [str(hardware), *sys.path])
    controller = _load_fl_path_function("fl_controller/device_FLStudioMCP.py", "_get_script_dir")
    assert controller() == hardware


def test_piano_roll_uses_installed_onedrive_script_when_no_env(monkeypatch, tmp_path):
    monkeypatch.delenv("FL_STUDIO_USER_DATA_DIR", raising=False)
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    scripts = (
        tmp_path
        / "OneDrive"
        / "Documents"
        / "Image-Line"
        / "FL Studio"
        / "Settings"
        / "Piano roll scripts"
    )
    scripts.mkdir(parents=True)
    (scripts / "ComposeWithLLM.pyscript").write_text("# installed\n", encoding="utf-8")
    script = _load_fl_path_function("scripts/ComposeWithLLM.pyscript", "get_script_dir")
    assert Path(script()) == scripts


def test_midi_command_file_cannot_be_overwritten_while_waiting(monkeypatch, tmp_path):
    monkeypatch.setenv("FL_STUDIO_MCP_SETTINGS_DIR", str(tmp_path))
    connection = midi_connection.MIDIConnection()
    monkeypatch.setattr(connection, "ensure_connected", lambda: None)
    monkeypatch.setitem(
        sys.modules, "mido", types.SimpleNamespace(Message=lambda *args, **kwargs: object())
    )
    connection._port = types.SimpleNamespace(send=lambda message: None)

    first_waiting = threading.Event()
    finish_first = threading.Event()

    def wait_for_response(_timeout):
        action = json.loads(connection._command_file.read_text(encoding="utf-8"))["action"]
        if action == "first":
            first_waiting.set()
            assert finish_first.wait(2), "First transaction did not complete"
        return {"success": True, "action": action}

    monkeypatch.setattr(connection, "_wait_for_response", wait_for_response)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(connection.send_command, "first")
        assert first_waiting.wait(1)
        second = pool.submit(connection.send_command, "second")
        time.sleep(0.06)
        assert json.loads(connection._command_file.read_text(encoding="utf-8"))["action"] == "first"
        finish_first.set()
        assert first.result(timeout=2)["action"] == "first"
        assert second.result(timeout=2)["action"] == "second"


def test_multiple_midi_instances_share_one_transaction_lock(monkeypatch, tmp_path):
    monkeypatch.setenv("FL_STUDIO_MCP_SETTINGS_DIR", str(tmp_path))
    first = midi_connection.MIDIConnection()
    second = midi_connection.MIDIConnection()
    assert first._command_lock is second._command_lock
