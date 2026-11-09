"""DAWproject 1.0 -> bounded, DAW-agnostic musical project graph.

This adapter is independent of vendor Java/GPL code. It parses the publicly
documented XML/ZIP interchange format without loading plug-ins or extracting
user files. All third-party states remain opaque.
"""

from __future__ import annotations

import hashlib
import math
import zipfile
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree as ET

GRAPH_VERSION = "universal-musical-project/1"
MAX_SOURCE_BYTES = 512 * 1024 * 1024
MAX_XML_BYTES = 16 * 1024 * 1024
MAX_MEMBERS = 2048
MAX_ELEMENTS = 250_000
MAX_CLIPS = 10_000
MAX_NOTES = 200_000
MAX_RESOURCES = 2048


def _tag(element: ET.Element) -> str:
    """Return the local XML tag name, without any namespace prefix."""
    return element.tag.rsplit("}", 1)[-1]


def _child(node: ET.Element | None, tag: str) -> ET.Element | None:
    """Return the first directly nested element with the requested local tag."""
    if node is None:
        return None
    return next((item for item in node if _tag(item) == tag), None)


def _descendants(node: ET.Element, tag: str):
    """Iterate matching descendants, including nested clip content."""
    return (item for item in node.iter() if _tag(item) == tag)


def _number(text: str | None, name: str, *, default: float | None = None) -> float:
    """Parse finite numeric XML attributes with an explicit bounded range."""
    if text is None:
        if default is None:
            raise ValueError(f"Required DAWproject number missing: {name}")
        return default
    try:
        value = float(text)
    except ValueError as exc:
        raise ValueError(f"Invalid DAWproject number: {name}") from exc
    if not math.isfinite(value) or abs(value) > 1e12:
        raise ValueError(f"Invalid or unbounded DAWproject number: {name}")
    return value


def _resource_path(path: str) -> str:
    """Only accept portable, ZIP-relative paths; never visit host paths."""
    if not path or "\\" in path or "\x00" in path or ":" in path:
        raise ValueError("Unsafe DAWproject resource path")
    p = PurePosixPath(path)
    if p.is_absolute() or any(part in ("..", ".", "") for part in path.split("/")):
        raise ValueError("Unsafe DAWproject resource path")
    return str(p)


def _hash_file(path: Path) -> str:
    """Hash source bytes incrementally without buffering the entire file."""
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _zip_inventory(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    """Validate bounded ZIP members before trusting any embedded resource path."""
    infos = archive.infolist()
    if len(infos) > MAX_MEMBERS:
        raise ValueError("DAWproject ZIP exceeds member limit")
    if sum(item.file_size for item in infos) > MAX_SOURCE_BYTES:
        raise ValueError("DAWproject ZIP exceeds uncompressed-size limit")
    index: dict[str, zipfile.ZipInfo] = {}
    folded: set[str] = set()
    for item in infos:
        if item.is_dir():
            continue
        name = _resource_path(item.filename)
        if name.casefold() in folded:
            raise ValueError("DAWproject ZIP has duplicate or case-colliding member names")
        folded.add(name.casefold())
        # Unix symbolic links can point outside the archive after extraction.
        if (item.external_attr >> 16) & 0o170000 == 0o120000:
            raise ValueError("DAWproject ZIP symbolic-link members are unsupported")
        index[name] = item
    if "project.xml" not in index:
        raise ValueError("DAWproject ZIP lacks project.xml")
    if index["project.xml"].file_size > MAX_XML_BYTES:
        raise ValueError("DAWproject project.xml exceeds XML size limit")
    return index


def read_dawproject(path: str) -> dict:
    """Return a versioned graph with explicit unknowns and source provenance."""
    source = Path(path).expanduser().resolve(strict=True)
    if not source.is_file() or source.suffix.lower() != ".dawproject":
        raise ValueError("Expected a .dawproject regular file")
    if source.stat().st_size > MAX_SOURCE_BYTES:
        raise ValueError("DAWproject source exceeds size limit")
    with zipfile.ZipFile(source) as archive:
        index = _zip_inventory(archive)
        xml = archive.read(index["project.xml"])
        if b"<!DOCTYPE" in xml.upper() or b"<!ENTITY" in xml.upper():
            raise ValueError("DTD and XML entities are not permitted")
        try:
            root = ET.fromstring(xml)
        except ET.ParseError as exc:
            raise ValueError("Malformed DAWproject project.xml") from exc
        if _tag(root) != "Project":
            raise ValueError("DAWproject XML root must be Project")
        if sum(1 for _ in root.iter()) > MAX_ELEMENTS:
            raise ValueError("DAWproject exceeds XML element limit")
        version = root.get("version")
        if version != "1.0":
            raise ValueError(f"Unsupported DAWproject version {version!r} (requires 1.0)")
        application = _child(root, "Application")
        transport = _child(root, "Transport")
        tempo = _child(transport, "Tempo")
        signature = _child(transport, "TimeSignature")
        bpm = _number(tempo.get("value") if tempo is not None else None, "Tempo.value", default=120)
        if not 1 <= bpm <= 1000:
            raise ValueError("DAWproject tempo outside supported range")
        numerator = int(signature.get("numerator", "4")) if signature is not None else 4
        denominator = int(signature.get("denominator", "4")) if signature is not None else 4
        if numerator < 1 or numerator > 255 or denominator not in (1, 2, 4, 8, 16, 32, 64):
            raise ValueError("Invalid DAWproject time signature")
        result: dict = {
            "schema": GRAPH_VERSION,
            "source": {
                "format": "dawproject",
                "version": version,
                "application": application.get("name") if application is not None else None,
                "application_version": application.get("version")
                if application is not None
                else None,
                "sha256": _hash_file(source),
                "bytes": source.stat().st_size,
            },
            "transport": {
                "bpm": bpm,
                "signature": [numerator, denominator],
                "tempo_automation": [],
            },
            "tracks": [],
            "clips": [],
            "automation": [],
            "markers": [],
            "resources": [],
            "diagnostics": [],
            "read_only": True,
        }
        structure = _child(root, "Structure")
        ids: set[str] = set()
        channels: set[str] = set()
        parent_of = {child: parent for parent in root.iter() for child in parent}
        if structure is not None:
            for node in _descendants(structure, "Track"):
                if len(result["tracks"]) >= 1024:
                    raise ValueError("Too many DAWproject tracks")
                track_id = node.get("id")
                if not track_id or track_id in ids:
                    raise ValueError("Missing or duplicate DAWproject track ID")
                ids.add(track_id)
                parent = parent_of.get(node)
                while parent is not None and _tag(parent) != "Track":
                    parent = parent_of.get(parent)
                parent_track_id = parent.get("id") if parent is not None else None
                channel = _child(node, "Channel")
                channel_id = channel.get("id") if channel is not None else None
                if channel_id:
                    if channel_id in channels:
                        raise ValueError("Duplicate DAWproject channel ID")
                    channels.add(channel_id)
                devices = []
                if channel is not None:
                    container = _child(channel, "Devices")
                    if container is not None:
                        for dev in container:
                            if len(devices) >= 128:
                                raise ValueError("Too many devices on DAWproject track")
                            state = _child(dev, "State")
                            devices.append(
                                {
                                    "kind": _tag(dev),
                                    "id": dev.get("id"),
                                    "name": dev.get("deviceName") or dev.get("name"),
                                    "device_id": dev.get("deviceID"),
                                    "role": dev.get("deviceRole"),
                                    "state_path": state.get("path") if state is not None else None,
                                    "loaded": dev.get("loaded"),
                                }
                            )

                def param(name: str):
                    """Read one optional mixer parameter from the channel's XML."""
                    item = _child(channel, name)
                    return _number(item.get("value"), name) if item is not None else None

                result["tracks"].append(
                    {
                        "id": track_id,
                        "name": node.get("name"),
                        "content_type": node.get("contentType"),
                        "parent_track_id": parent_track_id,
                        "channel_id": channel_id,
                        "role": channel.get("role") if channel is not None else None,
                        "destination": channel.get("destination") if channel is not None else None,
                        "volume": param("Volume"),
                        "pan": param("Pan"),
                        "mute": _child(channel, "Mute").get("value")
                        if _child(channel, "Mute") is not None
                        else None,
                        "devices": devices,
                        "sends": [
                            dict(s.attrib) for s in _descendants(_child(channel, "Sends"), "Send")
                        ]
                        if _child(channel, "Sends") is not None
                        else [],
                    }
                )
        for track in result["tracks"]:
            dest = track["destination"]
            if dest and dest not in channels:
                result["diagnostics"].append(
                    {"kind": "unresolved_routing", "track_id": track["id"], "destination": dest}
                )
        arrangement = _child(root, "Arrangement")
        if arrangement is not None:
            for group in _descendants(arrangement, "Markers"):
                for marker in _descendants(group, "Marker"):
                    if len(result["markers"]) >= MAX_CLIPS:
                        raise ValueError("Too many DAWproject markers")
                    result["markers"].append(dict(marker.attrib))
            for ta in _descendants(arrangement, "TempoAutomation"):
                for point in _descendants(ta, "RealPoint"):
                    result["transport"]["tempo_automation"].append(dict(point.attrib))
            total_notes = 0
            for points in _descendants(arrangement, "Points"):
                if len(result["automation"]) >= MAX_CLIPS:
                    raise ValueError("Too many DAWproject automation lanes")
                controls = [dict(p.attrib) for p in points if _tag(p).endswith("Point")]
                if len(controls) > MAX_NOTES:
                    raise ValueError("Too many DAWproject automation points")
                result["automation"].append(
                    {
                        "target": points.get("target"),
                        "unit": points.get("timeUnit"),
                        "points": controls,
                    }
                )

            def walk(node: ET.Element, track: str | None, unit: str, depth: int) -> None:
                """Walk arrangement lanes and capture source clips with track ownership."""
                nonlocal total_notes
                if depth > 32:
                    raise ValueError("DAWproject lane nesting exceeds limit")
                tag = _tag(node)
                if tag in ("Lanes", "Clips"):
                    track = node.get("track") or track
                    unit = node.get("timeUnit") or unit
                if tag == "Clip":
                    if len(result["clips"]) >= MAX_CLIPS:
                        raise ValueError("Too many DAWproject clips")
                    start = _number(node.get("time"), "Clip.time")
                    length = _number(node.get("duration"), "Clip.duration", default=0)
                    if start < 0 or length < 0:
                        raise ValueError("Negative DAWproject clip time/duration")
                    notes = []
                    for entry in _descendants(node, "Note"):
                        total_notes += 1
                        if total_notes > MAX_NOTES or len(notes) >= MAX_NOTES:
                            raise ValueError("Too many DAWproject notes in clip")
                        key = int(entry.get("key", "-1"))
                        channel_number = int(entry.get("channel", "0"))
                        if not 0 <= key <= 127 or not 0 <= channel_number <= 15:
                            raise ValueError("Invalid DAWproject MIDI note key/channel")
                        time = _number(entry.get("time"), "Note.time")
                        duration = _number(entry.get("duration"), "Note.duration")
                        vel = _number(entry.get("vel"), "Note.vel", default=1)
                        if time < 0 or duration <= 0 or not 0 <= vel <= 1:
                            raise ValueError("Invalid DAWproject MIDI note values")
                        notes.append(
                            {
                                "time": time,
                                "duration": duration,
                                "key": key,
                                "channel": channel_number,
                                "velocity": vel,
                                "release": entry.get("rel"),
                                "expressions": [
                                    {
                                        "lane": dict(points.attrib),
                                        "points": [dict(point.attrib) for point in points],
                                    }
                                    for points in _descendants(entry, "Points")
                                ],
                            }
                        )
                    audio = []
                    for entry in _descendants(node, "Audio"):
                        file = _child(entry, "File")
                        audio.append(
                            {
                                "path": file.get("path") if file is not None else None,
                                "external": file.get("external") if file is not None else None,
                                "sample_rate": entry.get("sampleRate"),
                                "channels": entry.get("channels"),
                                "algorithm": entry.get("algorithm"),
                            }
                        )
                    result["clips"].append(
                        {
                            "track_id": track,
                            "name": node.get("name"),
                            "start": start,
                            "duration": length,
                            "time_unit": unit,
                            "content_time_unit": node.get("contentTimeUnit") or unit,
                            "play_start": _number(
                                node.get("playStart"), "Clip.playStart", default=0
                            ),
                            "loop_start": node.get("loopStart"),
                            "loop_end": node.get("loopEnd"),
                            "reference": node.get("reference"),
                            "notes": notes,
                            "audio": audio,
                            "nested_clips": [
                                dict(n.attrib) for n in _descendants(node, "Clip") if n is not node
                            ],
                            "warps": [dict(w.attrib) for w in _descendants(node, "Warp")],
                            "fade": {
                                k: node.get(k)
                                for k in (
                                    "fadeTimeUnit",
                                    "fadeInTime",
                                    "fadeOutTime",
                                    "playStop",
                                    "enable",
                                )
                                if node.get(k) is not None
                            },
                        }
                    )
                    # Nested Clips can hold offset media/warps. Preserve the outer
                    # playback envelope rather than double-counting inner clips.
                    return
                for child in node:
                    walk(child, track, unit, depth + 1)

            lanes = _child(arrangement, "Lanes")
            if lanes is not None:
                walk(lanes, None, lanes.get("timeUnit", "beats"), 0)
            for clip in result["clips"]:
                if clip["track_id"] not in ids:
                    result["diagnostics"].append(
                        {
                            "kind": "unresolved_clip_track",
                            "track_id": clip["track_id"],
                            "clip": clip["name"],
                        }
                    )
        # External resources can legally name absolute host paths; retain the
        # reference for diagnosis but never attempt to read, hash or copy it.
        used_paths = set()
        for clip in result["clips"]:
            for item in clip["audio"]:
                path = item.get("path")
                if item.get("external", "false") in ("true", "True", "1"):
                    result["diagnostics"].append(
                        {"kind": "external_resource_not_read", "path": path, "clip": clip["name"]}
                    )
                elif path:
                    used_paths.add(path)
        used_paths.update(
            dev["state_path"]
            for track in result["tracks"]
            for dev in track["devices"]
            if dev["state_path"]
        )
        if len(used_paths) > MAX_RESOURCES:
            raise ValueError("Too many DAWproject referenced resources")
        for raw in sorted(used_paths):
            safe = _resource_path(raw)
            entry = index.get(safe)
            if entry is None:
                result["diagnostics"].append({"kind": "missing_resource", "path": safe})
                result["resources"].append({"path": safe, "available": False})
                continue
            with archive.open(entry) as stream:
                digest = hashlib.sha256()
                while chunk := stream.read(1024 * 1024):
                    digest.update(chunk)
            result["resources"].append(
                {
                    "path": safe,
                    "available": True,
                    "bytes": entry.file_size,
                    "sha256": digest.hexdigest(),
                }
            )
        if not result["tracks"]:
            result["diagnostics"].append({"kind": "no_tracks"})
        if not result["clips"]:
            result["diagnostics"].append({"kind": "no_arrangement_clips"})
        return result
