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
