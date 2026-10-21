# Change log

## Unreleased

## 0.7.0 - 2026-10-21

- Keep one capture owner per review request and share the frozen context with delegates; repair drafts in the retained session and stop after terminal failures without automatic recapture.
- Record fetch attempt/success and requested/synced branch counts separately from branch entries and HTTP transport requests.
- Add an ancestor no-op coverage policy with explicit reviewer judgments for all seven dependency categories and supporting repositories; retain snapshots, raw patches, catalog entries and unverified environment status.
- Seal the captured base-to-head commit path and independently verify its hashes and parent links before granting ancestor exemptions, without adding historical trees or semantic review work.
- Add inclusive fixed-read ranges and saved search-ID filtering while preserving legacy output, batch errors, original indexes, complete evidence and search records.
- Document exact request and output shapes. This change validates helper regressions and makes no new model-quality benchmark claim; Context, Draft and Report remain v3.
