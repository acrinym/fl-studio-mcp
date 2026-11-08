# Native FL Studio intelligence: architecture roadmap

**Mission:** Provide project-aware inspection and verified operations for FL Studio without generating redundant button-level tools. The architecture favors safe, composable adapters over a single fragile access path.

## Distinct surfaces

| Surface | What it holds | Safe access path | Current state |
| --- | --- | --- | --- |
| FLP | Project music, tempo, patterns/channels, plugin wrapper state | Bounded FLhd/FLdt inspection; later typed graph/diff | Header and **read-only event summary** |
| ZIP | FLP plus bundled audio/assets (not necessarily third-party sampler libraries) | ZIP listing and bounded in-memory FLP inspection; **no extraction** | Implemented read-only |
| FST | Channel, plugin, mixer-insert or automation state, depending on format code | FLhd/FLdt framing; classify marker; vendor payload opaque | Implemented read-only summary |
| FSC | Piano-roll/score data | Research before adopting | Not implemented |
| Third-party plugin state | VST/VST3-specific chunks, often proprietary | Plugin parameter APIs first; versioned RE only with fixtures | Not decoded |
| Project settings | Some fields in FLP: tempo, pitch, pan law, etc. | Project-level events; compare to live project status | Initial **read-only** fields |
| Global FL Studio settings | Audio/MIDI/general/file environment; registry and user data | Explicitly separate, allowlisted audit; preserve licenses | Not accessed or changed |
| FL Studio UI | Controls absent from scripting API | Authorized GUI automation plus read-back | Future adapters |

**Important:** FLT and FLS are **not** treated as synonymous with FST without evidence. FST is the established FL Studio state/preset format.

## Implemented milestone A: safe structural understanding

- The fl_inspect_native_file(path) tool now returns FLP/FST header, format classification, bounded event counts, channel/pattern start-event counts, 24-byte note-record counts (structural only), FL version, observed plugin labels, opaque plugin-state byte count and initial project setting values.
- For ZIPs it inventories members (up to 100 member names returned) and inspects embedded FLPs without writing or extracting assets.
- Parser validates declared stream lengths and bounded varints; rejects all-NUL event data and malformed/truncated values; bounds ZIP expansion and event count; never returns raw proprietary plugin-state chunks or license fields.
- This is **not** a complete semantic model: repeated IDs can be context-dependent; note counts do not establish channel ownership; initial coarse tempo may be superseded by automation; missing settings are not assumed defaults.
- No FLP/FST serializer, save operation, registry operation, plugin loading, or FL Studio GUI mutation exists in this milestone.

## Alternative mechanisms: bypass unsupported API operations

| User intention | Blocked native route | Lateral execution candidate | Required verification |
| --- | --- | --- | --- |
| Find notes across a song | MIDI controller lacks complete project graph | Parse FLP notes/arrangements off-line, resolve pattern and channel IDs | Compare live project read-only |
| Add an instrument | Controller API cannot add generators | Plugin database plus an authorized GUI adapter | Channel exists and instrument matches |
| Change a third-party synth patch | Wrapper API misses internal vendor parameters | Load alternate preset via GUI/plugin API; later qualified FST editing | Plugin displays patch; survives save/reopen |
| Rearrange a clip | Playlist insertion not exposed | GUI or experimental **copy-only** FLP arrangement patch | Reopen copied project, screenshot and semantic diff |
| Recover damaged preset | Plugin blob/version issues | Compare known-good FST, backups, native vendor export | Check load behavior; preserve original |
| Change global audio device | Not a project-level event | Authorized app setting adapter, supported UI preferred | Read back selected device after restart |

Operating abstraction: **one musical intention -> inspected candidates -> authorized mechanism -> verification receipt**, not a tool per menu item.

## Format research: references and remaining gaps

1. **PyFLP** documents the FLhd/FLdt header, four event size classes and varint payloads, plus models for arrangements, channel rack, mixer inserts, notes and plugins. Sources: https://pyflp.readthedocs.io/en/stable/architecture/flp-format.html and https://pyflp.readthedocs.io/en/stable/reference/project.html . It is a reference/comparator, not a guaranteed serializer. Review **GPL** compatibility before copying its code into this MIT repository.
2. **Image-Line** documents zipped project contents and limitations of third-party sampler content: https://www.image-line.com/fl-studio-learning-content/fl-studio-online-manual/html/fformats_project.htm .
3. **Image-Line** distinguishes global system settings from project-specific settings: https://ww2.image-line.com/fl-studio-learning/fl-studio-online-manual/html/envsettings.htm .
4. **Image-Line** confirms FST presets and Settings subfolders: https://www.image-line.com/fl-studio-learning/fl-studio-online-manual/html/app_update.htm .
5. Still to investigate: VST2/VST3 parameter and chunk handling, FL scripting API differences by version, DAW interchange, production-safe GUI automation, fixtures, audio-to-MIDI separation and transcription. These are **research candidates**, not claimed integrations.

## Safety and failure-mode gates

- **Failure points:** unknown events, plugin-version-specific chunks, compound project references, FL version upgrades, corrupted/null-filled sync files, GUI focus and dialogs.
- **Failure isolation:** inspect without FL Studio running. No byte-level rewrite until unknown records are preserved exactly and canonical copies pass re-open tests. Maintain immutable source and hashes.
- **Versioning:** use sanitized synthetic or user-authorized fixtures saved by different FL versions; demonstrate consistency before write support.
- **Privacy:** registry license/activation values never appear in reports. Avoid OneDrive sync mirror and null-filled files while recovery is pending.
- **Transactions:** future modifications create a disposable copy, calculate before/after semantic diff, save a receipt, reopen the copy in FL Studio and roll back on mismatch.
- **Tests:** synthetic corruption/truncation/ZIP traversal fixtures plus read-only checks against authorized real-world FST and zipped-project fixtures. Never mutate originals.

## Next milestones (not yet implemented)

**B. Project graph (in progress):** optional read-only pattern-to-rack-channel note graph is implemented with bounded output. The new optional playlist graph maps documented 32/60-byte arrangement clip records to pattern IDs, 1-based playlist tracks when a 199/500-track schema is present, and each pattern's decoded rack-channel note counts. Unknown layouts are reported, not guessed. Still needed: actual channel/instrument labels, mixer and plugin-wrapper graph, structural diff, and live FL Studio parity tests.

**C. State-aware editing:** selective field-scoped changes on copies, preserving unknown chunks, formal round-trip and FL Studio project-open validation. Change plugin patches only when semantics are independently established. Do not edit PPQ until dependent positions are recalculated.

**D. System settings inventory:** allowlisted, read-only named settings with provenance, masked license values, and **separate explicit approval before any registry writes**. C:/D: junction migration is a different mission.

**E. Hybrid execution:** choose MIDI, piano-roll script, authorized GUI automation, or qualified copy-only native editing. Every mutation ends with verified read-back.

**F. Audio-to-MIDI:** MP3/MP4 audio extraction, polyphonic transcription, optional stems, tempo alignment, confidence reporting and MIDI import; future audio clip/stem editing surfaces beyond MIDI.

**Protection:** Never modify source projects, plugin licenses, activation settings, cloud-sync files, or the live DAW during this read-only milestone. All write operations require explicit authorization and verification.
