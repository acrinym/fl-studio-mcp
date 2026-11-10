# Universal Musical Project Engine — DAWproject reconstruction

## Implemented boundary

The first DAW-independent adapter recognizes **DAWproject 1.0** containers
(`.dawproject` ZIP with `project.xml`) and produces
`universal-musical-project/1`. This is **not a universal converter yet**:
it is a checked, read-only project interpreter and a reversible reconstruction
handoff for FL Studio, with explicit per-feature fidelity accounting.

* `fl_inspect_universal_project(path)` never extracts assets or controls a DAW.
  It maps source identity and SHA-256, tempo, meter, tracks/group parents, channel
  destinations and sends, device IDs/wrapper state references, clip instances,
  positioned notes, nested audio references, warps, automation and markers where
  represented, preserving unimplemented attributes for later reconstruction.
* `fl_build_reconstruction_bundle(path, output_dir)` **writes a brand-new folder**
  that must not exist already: `project_graph.json`, per-source-track MIDI
  timelines, exact hashed copies of referenced in-ZIP resources under
  `resources/`, and `fidelity_receipt.json`. Temporary creation is on the
  same destination volume, with cleanup on failure. Source bytes are rehashed
  before publication. It never creates/overwrites an FLP.
* Embedded CLAP/VST or other vendor state remains opaque. Plugin substitutes,
  audio stretch/warps, per-note expression, source clip instancing, routing,
  note automation, and tempo ramps are **NOT rebuilt inside FL Studio**.
  Nested note clips without a verified time mapping are counted but skipped,
  never exported at unqualified tick positions.
* MIDI clips are flattened per source track into positioned time events; effects
  and audio source files are retained in their original bytes, not recreated as
  an FL Studio mixer/Playlist project. Missing files and unsupported timing
  cause explicit diagnostics/skips; they are not faked as a successful import.
* An independent optional **native FLP template backend** now exists:
  `fl_build_native_flp_from_template` writes note patterns, Playlist placements
  and static tempo into a **new copy** of a deliberately blank FL-authored FLP.
  It uses MIT-licensed flpkit 0.9.0 (Python 3.11+), has per-clip and per-note
  parser readback, and an explicit native fidelity receipt. A new standalone
  `fl_audit_native_reconstruction` MCP tool independently verifies an existing
  bundle against the unchanged source DAWproject, FLP and declared source and
  mapped feature inventories. This source-to-FLP audit also runs before new
  output is published. It is a limited
  reconstruction rather than full cross-DAW equivalence. See
  [native template backend](NATIVE_FL_TEMPLATE_BACKEND.md).
* No live FL Studio project, licensing, user VST directories or originals are
  modified. Native FL Studio application open/save/reopen validation and complete
  reconstruction **remain open** until separately authorized and qualified.

## Source-to-bundle contract

```text
DAWproject .dawproject (source unchanged; SHA-256 recorded)
  -> bounded ZIP validation, DTD-rejecting XML parser, 1.0 schema gate
  -> versioned graph (track/channel/clip/device/automation/resource identities)
  -> feature capability planning + explicit unmapped semantics
  -> per-track Standard MIDI Files + SHA-256-verified original media/state
  -> source/file hashes, diagnostics and complete fidelity receipt
  -> MANUAL operator import into a fresh FL Studio project
  -> future native read-back/semantic comparison (NOT implemented)
```

**Source safety:** 2,048 members maximum; 512 MiB maximum declared uncompressed
ZIP content; 16 MiB maximum project XML; event/track/clip limits; no DTD, XML
entities, duplicate/case-colliding ZIP filenames, absolute paths, `..` traversal,
or ZIP symbolic links. No external user resource is fetched or read. Graph does
not attempt to deserialize vendor plugin state.

**Fidelity vocabulary:** Every mapped feature is either preserved exactly as
bytes, converted with a declared mapping, approximated/lossy, unavailable,
unknown/unverified, or explicitly outside the current backend. An exported MIDI
file is **not** evidence that an FL Studio project exists or that a synthesizer,
clip, bus or automation lane is correctly restored. DAWproject external
resources are retained as references but never read from absolute host paths;
they receive explicit unavailable-resource diagnostics.

## Qualification sources

1. Synthetic but official-schema-shaped DAWproject ZIPs are generated in
   `tests/test_universal_project.py`; they exercise source hashes, track
   identities, clip positions, MIDI note positions, tempo and meter, audio/plugin
   byte copies, format rejects, bounds, malicious paths and incomplete-fidelity
   accounting. All synthetic test writes use temporary directories.
2. An independent **public** Bitwig Studio 5.1.6 DAWproject fixture is available:
   `https://github.com/bkbkb-net/dawproject/blob/main/assets/tests/canon.dawproject`.
   At this milestone the 1,267,977-byte fixture had SHA-256
   `ca106382d181b88268a4ae39d1e524a11167facd8b69d147125f8fd736a57f89`.
   It yielded 4 tracks, 2 clips, 27 note events and 1 audio resource; a
   reconstruction bundle emitted 27 notes as MIDI and preserved source audio.
   This was **native file processing, not FL Studio GUI/open verification**.
3. Future acceptance before claiming full user-requested cross-DAW conversion:
   use a writable new FL Studio project/copy, import/reconstruct each mapped
   instrument/clip/effect/automation/channel, save FLP, reopen with actual DAW,
   perform independent semantic read-back against source graph, and record every
   unsupported or substituted feature. Requires separately approved native
   application manipulation.

## Technical references and licensing

* Official format and schema: https://github.com/bitwig/dawproject (Project.xsd).
* MIDI format handled through existing MIT-compatible `mido` dependency.
* DawVert is GPL-3.0; no DawVert GPL code is copied into this MIT project.
  A separate-process integration would require licensing review.

## No blanket authorization

This module is a public-source format reader and reconstruction-pack writer.
It is not authority to modify installed FL Studio, original .flp files, VST
registrations, registry/license state, OneDrive recovery, or external collections.
