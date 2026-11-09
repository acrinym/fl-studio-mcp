# What I Learned — FL Studio MCP

## 2026-11-09 — Universal project export cannot imply native FLP reconstruction

- **Issue / PR:** Universal Musical Project Engine, feature branch
  `feature/dawproject-universal-reconstruction`; PR pending review.
- **Symptom/gap:** Our native FLP parser can inspect structures but has no
  qualified writer. Standard MIDI can restore notes but not complete FL Studio
  clip instances, audio warps, automation, proprietary instruments and buses.
- **Root cause:** Reading an undocumented binary event stream is not the
  inverse of safely serializing/reopening that application version's format;
  standalone MIDI carries only a subset of project semantics.
- **Solution:** Implement a bounded, versioned DAWproject graph with explicit
  track/clip/media/device relationships, preserve referenced embedded resources
  byte-for-byte, create importable per-track MIDI and a receipt that declares
  which semantics are unmapped. Always report `native_flp_created: false`
  and `fl_studio_open_verified: false`.
- **Changed files:** `src/fl_studio_mcp/utils/universal_project.py`,
  `src/fl_studio_mcp/utils/reconstruction_bundle.py`,
  `src/fl_studio_mcp/tools/universal_project.py`, registrations and tests.
- **Verification:** Synthetic source-to-bundle E2E tests; actual published
  Bitwig 5.1.6 `canon.dawproject` produced 27 of 27 notes with 1 preserved
  audio sample and no file errors. Source SHA-256:
  `ca106382d181b88268a4ae39d1e524a11167facd8b69d147125f8fd736a57f89`.
  No FL Studio live app or native saved FLP has been tested.
- **Remaining:** Actual source-format-to-native-FLP roundtrip and editable
  clip/effects/automation reconstruction are open and require proper runtime
  qualification; a successful package export is not completion.
- **Regression guard:** Tests assert source and media hashes, MIDI event
  positions, limitation flags, malformed input rejection, missing resources,
  unsupported clip time, loops and unsafe ZIP paths.
- **Commit/merge evidence:** Tracked in linked feature branch; do not call
  merged until final review and normal two-parent merge receipt exist.

## 2026-11-09 — Orphaned clip notes lost from attempted export count

- **Issue:** Discovered while implementing universal reconstruction receipt.
- **Symptom/gap:** Source clips referencing a missing track could be ignored
  entirely when summing only notes of enumerated destination tracks, yielding
  a false `source_notes=0` and apparent success despite source notes existing.
- **Root cause:** Source-note accounting occurred inside the per-known-track
  exporter loop rather than over the complete source clip graph.
- **Solution:** Count source notes globally from all clips; separately count
  exported events, detect unresolved clip track references and issue explicit
  lossy status and diagnostic.
- **Changed files:** `src/fl_studio_mcp/utils/reconstruction_bundle.py`,
  `src/fl_studio_mcp/utils/universal_project.py`,
  `tests/test_universal_project.py`.
- **Verification:** Dedicated `test_orphan_clip_notes_are_counted_as_unconverted`
  checks 3 source notes, 0 exported notes, missing-track diagnostic and loss
  receipt. Full Windows and WSL suites both passed 264 tests at the source state
  before commit; final-head smoke and Git diff checks remain required.
- **Remaining:** An operator must resolve missing track links before automatic
  native DAW reconstruction.
- **Regression guard:** Preserve this as a mandatory source-vs-export cardinality
  test; never derive source cardinality solely from successful exports.
- **Commit/merge evidence:** Feature branch pending PR/review.

## 2026-11-09 — WSL CI missing MIDI package while Windows passed

- **Issue:** Initial new-source qualification WSL import failed.
- **Symptom/gap:** WSL pytest collection raised `ModuleNotFoundError: mido`
  although the Windows project virtualenv had the dependency.
- **Root cause:** Existing shared/global WSL pytest interpreter did not include
  project's runtime MIDI dependency. Reusing shared Windows `.venv` in WSL is
  explicitly prohibited because it can replace its interpreter/packages.
- **Solution:** Create a reusable, independent WSL environment at
  `~/.venvs/flstudio-mcp-ci` with `uv venv` and install `pytest`,
  `mido` and `ruff`. Execute WSL source via canonical
  `/mnt/d/GitHub/AI/FLStudioMCP` path (no duplicate repo/worktree).
- **Changed files:** No product source change required for environment;
  targeted CI uses the reusable WSL toolchain.
- **Verification:** Targeted WSL suite passed 11 tests after environment
  restoration; subsequently Windows and WSL full suites each passed
  264 tests at the source state before commit.
- **Remaining:** Full WSL tests may require additional runtime packages;
  do not silently suppress them.
- **Regression guard:** Keep WSL CI executable isolated from Windows `.venv`;
  check interpreter package coverage before concluding source regression.
- **Commit/merge evidence:** Environment-only corrective action, not a source PR.

## 2026-11-09 — CodeRabbit PR #8: nested notes exported at incorrect outer-clip timestamps

- **Issue/PR:** #8, review thread `PRRT_kwDOVW6CJs61d1x5`; initial source `c8c8154`.
- **Symptom:** Notes found under nested `Clip` nodes were flattened onto the outer clip using the wrong starting position, despite a positive conversion-fidelity receipt.
- **Root cause:** The reader's recursive `_descendants(..., "Note")` collected inner notes, while the MIDI writer had no nested clip offset/play-window translation.
- **Solution:** The writer now excludes note-bearing clips with nested `Clip` descendants from MIDI export and records `nested_clip_timing_not_resolved`; it counts every original note as unconverted. This repairs the **false exact-fidelity claim**. Correctly rebuilding nested-clip playback remains a separate open implementation, not claimed fixed.
- **Changed files:** `src/fl_studio_mcp/utils/reconstruction_bundle.py`, `tests/test_universal_project.py`.
- **Verification:** `test_nested_note_clip_does_not_emit_wrong_midi_ticks` confirms inner clip notes are not emitted at false ticks and that the receipt reports loss; both full WSL and Windows suites passed 267 tests on the corrected source before commit.
- **Remaining:** Actual nested clip/timing reconstruction is open.
- **Prevention:** Preserve source-count vs exported-count invariant and explicit nested-timing test on every writer update.

## 2026-11-09 — CodeRabbit PR #8: malformed DAWproject archive escaped MCP handler

- **Issue/PR:** #8, review thread `PRRT_kwDOVW6CJs61d1x0`.
- **Symptom:** A corrupt `.dawproject` ZIP caused raw `BadZipFile` (and related deflate errors) to escape the inspection MCP tool; build and inspect tools handled failures inconsistently.
- **Root cause:** The two tool boundaries used different, incomplete exception tuples.
- **Solution:** One shared `_EXPECTED_ARCHIVE_ERRORS` tuple now handles relevant malformed ZIP and decompression errors in both MCP entrypoints, returning structured failures without treating invalid files as successful.
- **Changed files:** `src/fl_studio_mcp/tools/universal_project.py`, `tests/test_universal_project.py`.
- **Verification:** `test_corrupt_archive_returns_structured_error_from_both_tools` checks both return structured `BadZipFile` errors and do not create an output directory.
- **Remaining:** The underlying malformed source remains rejected, not silently repaired.
- **Prevention:** Regression test both wrappers whenever archive parsing behavior changes.

## 2026-11-09 — CodeRabbit PR #8: external audio path rejected entire source graph

- **Issue/PR:** #8, review thread `PRRT_kwDOVW6CJs61d1yF`.
- **Symptom:** A valid DAWproject with `File external="true"` and an absolute sample path failed the whole import due to the embedded-ZIP path sanitizer.
- **Root cause:** Embedded resource discovery did not distinguish **external references** from archive members.
- **Solution:** Preserve the `external` metadata, skip host-path I/O for external refs, add an `external_resource_not_read` diagnostic and an explicit unavailable/missing source-resource count in the fidelity receipt. Embedded resources are independently preserved.
- **Changed files:** `src/fl_studio_mcp/utils/universal_project.py`, `src/fl_studio_mcp/utils/reconstruction_bundle.py`, `tests/test_universal_project.py`.
- **Verification:** `test_external_dawproject_resource_is_not_read_from_host` confirms the graph loads, excludes host paths from read/copy, and still copies embedded plugin state.
- **Remaining:** Restoring external samples requires separate owner-approved asset resolution, not a filesystem guess.
- **Prevention:** Keep ZIP and host-resource classes distinct and reject host-file access by default.

## 2026-11-09 — CodeRabbit PR #8: track-hierarchy test was a no-op

- **Issue/PR:** #8 review nitpick and premerge Docstring Coverage check.
- **Symptom:** A test claimed to exercise nested track parentage, but its XML string replacement changed nothing; new functions also lacked descriptive docstrings.
- **Root cause:** The fixture never constructed a nested `Track`, and the code lacked explicit documentation coverage for changed functions.
- **Solution:** Insert a real nested child track in the fixture, assert parent ID and root parent absence, and document the helper/tool/test function contracts.
- **Changed files:** `tests/test_universal_project.py` and new universal project modules.
- **Verification:** Full WSL and Windows suites each passed 267 tests on corrected source, Ruff lint and format clean; independent exact-head rereview still required.
- **Remaining:** No claim of FL Studio-native interpretation until actual application reopen qualification.
- **Prevention:** Assert behavior exercised by each test name; prefer meaningful docstrings, not suppression of review checks.
