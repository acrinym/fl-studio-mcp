# Native FLP Template Backend — Experimental, Offline Qualification

## Implemented capability

The MCP tool **fl_build_native_flp_from_template(path, template_flp, output_dir)**
takes a DAWproject 1.0 source, an explicitly specified blank FL Studio-authored
template, and a previously nonexistent destination directory.

An optional MIT-licensed **flpkit 0.9.0** backend edits a **COPY** of that
template. It writes selected source notes into distinct native FL patterns,
places those patterns on FL Playlist tracks, sets the static tempo and project meter, and
re-reads the FLP binary. The result contains **reconstructed.flp**,
**native_fidelity_receipt.json** and **project_graph.json**.

The output is a native FLP binary, not a MIDI-only handoff. Its FLP parser
readback is compared with the project time signature and every planned note key, channel, pattern, tick
position and length, Playlist clip position/length/pattern/track, and tempo.
A mismatch fails and cleans up the unpublished output. FLP note velocities,
including duplicate-note multiplicities, are checked against declared
7-bit native velocity mapping. The builder also runs a separate source-to-FLP
semantic audit before publishing output.

**Real FL Studio application open/save/reopen is NOT verified.** FLP parser
readback is not proof that FL Studio will load the file, plugins will sound,
audio routes will work or the project will survive a native app re-save.
The receipt explicitly marks FL Studio open verification, resave verification
and complete automatic conversion as FALSE. It also contains source and mapped
feature inventories: native vs source clip/note counts, sample/audio clips,
instrument/effect devices, routing, sends, tempo automation, other automation,
time signature, warps, external-resource references and missing media.

## Inputs and safeguards

Provide a blank FL-authored FLP scaffold specifically made for conversion.
The template must contain enough empty instrument channels, pre-existing
pattern clips and Playlist tracks for the supported source content, plus
no inherited notes, audio clips or referenced samples. Insufficient capacity,
trial-saved templates and existing output directories are rejected. Never
pass an only copy of an important user project.

Python 3.11+ and the optional native-flp extra (flpkit==0.9.0) are required.
The main MCP still supports Python 3.10 without this extra; a missing backend
returns a structured error. FL Studio does NOT need to be running.

Example arguments:
    path: /projects/source.dawproject
    template_flp: /projects/blank-scaffold.flp
    output_dir: /projects/brand-new-output-folder

The source and template are SHA-256-checked before and after construction.
All output is staged in a sibling temporary directory and then published
only if the destination remains available and readback checks pass.
The source, template and user-installed FL Studio remain untouched.

## Fidelity limitations

Currently reconstructable: beat-time non-looping note clips, zero-based
MIDI note channel, per-clip native pattern mapping, Playlist placement,
source key/velocity/length, one source note-bearing track per template
channel, a single static tempo and the source's static project time signature. The meter is
patched only when the FL-authored scaffold already contains exactly one
FLP numerator event (0x11) and one denominator event (0x12). No proprietary
file reserialization or guessed event insertion occurs. The source meter is
independently reread during the source-to-FLP semantic audit. Native PPQ can
quantize timing; it is
reported.

NOT reconstructed: nested/looped/referenced clips, playStart offsets,
nonzero MIDI channels, expression/release, embedded and external audio,
warping or time-stretch, plugins and vendor preset state, mixer buses/sends,
routing, automation curves, tempo ramps, Playlist hierarchy, other arrangement
versions, and actual FL Studio app compatibility. Unsupported source notes
are counted and skipped explicitly, never emitted at invented positions.
All unknown source metadata remains in the graph JSON.

## Offline test fixture provenance

The committed tests/fixtures/native_flp_scaffold.flp is a **synthetic
composite**. It has NOT been saved or reopened by FL Studio.

- App-authored FL Studio 25.2.4 base: MIT dawhubapp/flpdiff,
  tests/corpus/re_base/fl25/base_empty.flp,
  SHA256 1b396047ddf61102a906e99cd01223c8099ee3e1e6c8ee01c2275ae225d6fd23.
- Playlist event source: MIT clarkipeng/flpkit,
  tests/golden/playlist-template-tail-inherited.flp,
  SHA256 20102c8c8f3202e82613bdfe194813f4a40674331be3cc20142a1486fc779a8.
- Construction: replace the empty FLdt Playlist event in the first binary
  with the second fixture's two-entry Playlist event. Preserve all other
  first-fixture bytes and update the FLdt chunk-length field.
- Result SHA256 9449554bbd72861b06aed33731372b61ccf76b9b84c467b4f9d9e5764c1cca98.

Tests qualify the actual offline source-to-native-binary-to-parser-readback
pipeline, non-overwrite behavior, invalid-input contracts and incomplete
fidelity receipts. They do not prove real app acceptance.

## Independently rerunning the semantic audit

The fourth MCP operation, **fl_audit_native_reconstruction**, accepts
**output_dir** (the completed reconstruction folder) and **source_path** (the
original DAWproject). It is read-only: no output is written, no host sample
paths are accessed, and FL Studio is not launched.

The auditor re-parses the actual original source; verifies SHA-256 for the
source, saved canonical graph and FLP; checks all declared clip indices and
selected note indices against the current parsed graph; compares every native
note pitch, position, length, velocity, channel and pattern with a multiset
rather than a uniqueness-losing set; checks Playlist placement, count and
tempo; and recomputes skipped content and feature inventories. False success
flags, omitted notes, altered source data or a forged FLP hash with musically
changed notes fail audit. A successful audit returns **verified: true**, with
**fl_studio_open_verified: false** and **complete_automatic_conversion: false**.

When creating a new FLP, the generator runs the same auditor *before* moving
the staged output into its final new directory. If independent verification
fails, the temporary output is removed rather than misrepresented as success.

## Licensing and next gate

This maintained fork remains MIT. The optional external flpkit package is
also MIT, and no GPL DawVert or PyFLP code was copied or vendored.
An authorized FL Studio runtime open, save-new-copy and reopen plus semantic
comparison is the required next gate before native-app conversion is claimed.
