# Native FL Studio format intelligence

Goal: Desktop Commander-style project understanding, then safe editing. HTE-Newest documentation from `C:\Users\User\Documents\Claude\HTE-Newest` guided this design; no HTE Python was run.

## First implementation
`fl_inspect_native_file(path)` reads `.flp`, `.fst` and `.zip` without modifying them. Validates FLhd/FLdt framing, reports header metadata and zipped project member inventory, with resource limits and no extraction. It does not decode musical events or opaque VST chunks yet. It cannot create/edit plugins or settings yet.

## Roadmap

First decode typed FL events read-only with byte offsets, version-aware fixtures, and preservation of unknown events. Next map channels, patterns, notes, playlist clips, mixer routing, plugin wrappers and vendor chunks. Build a project graph and project-to-project diff. Then investigate plugin preset state and distinguish `.fst` state from plugin database entries. Separately map application-wide settings (config, registry, user data) without disclosing license secrets. After backups and round-trip validators, introduce selective edits on copies, reopen in FL Studio and compare observable results. Use native scripting, GUI via Onyx Hands, or offline tools as appropriate, returning verified receipts. Later, add MP3/MP4 to MIDI via source separation and transcription with quality reporting.

## HTE Lateral and InvisiSynth reconnaissance
Investigate open-source FL event parsers and fixtures, Image-Line scripting APIs, VST state formats, DAW interchange, community preset tooling, and audio transcription. Evaluate licenses, current maintenance, accuracy and actual supported capabilities before integration. Do not invent tool support.

## Protection
No changes to FL Studio, registrations, OneDrive or original Town 04 project. No native project writes. No worktrees without approval.
