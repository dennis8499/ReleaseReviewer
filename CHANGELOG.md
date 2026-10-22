# Change log

## Unreleased

## 0.8.0 - 2026-10-22

- Seal packs through a streamed owned file with short snapshot.pack/snapshot.idx basenames, binding the Git checksum and both complete SHA-256 file hashes. Preserve historical snapshot filenames, source-independent validation, and unchanged Git configuration; reject unsupported Windows owned Git root depths before capture.
- Add independent persistent start/step/check/finish/status/cancel requests in an owned runtime outside Git repositories, with actual read/write/replace preflight before capture.
- Checkpoint successful operation IDs, retain a valid batch prefix after repairable input errors, and reuse verified saved search payloads only inside the same frozen context.
- Keep missing, unreadable and malformed author-controlled requests/draft files repairable in the same request. Return explicit nonterminal diagnostics without Git, preserve successful operation checkpoints, and keep owned manifest/checkpoint corruption terminal.
- Keep legacy full manifest verification as the default. Saved artifact views and draft checks use zero-Git metadata loading; request rounds validate each used repository once, and publication independently validates all snapshots and saved search records.
- Save an external staged/published receipt before cleanup. Retry returns the existing report, checks its static body, permits edits inside the human checklist, and cannot publish twice after a cleanup failure.
- Persist the same validation's authoritative review outcome before the report link. Published status/check preserve semantic gaps after cleanup without Git; incomplete and unknown published checks exit 3, while pending deployment checklists retain the validator's static completeness.
- Render engineering v2 drafts directly with the title and five review sections, keeping branch/SHA identity in the conclusion and deployment checklists open. Historical v1/unset layouts retain their original structure.
- Aggregate draft structure and missing coverage diagnostics without semantic approval. Preserve all seven dependency categories, entry-base first-use searches, supporting repository identities, fixed-source guarantees, and legacy CLI behavior.
- Check existing risk/dependency final-source, configuration-definition and merge-parent own-path/version citation relationships during metadata diagnostics, before the full publication validation.
- Each request seals its own snapshots and fetches its explicit remote branches. This release adds no cross-request fetch or pack cache and makes no new performance claim.

## 0.7.0 - 2026-10-21

- Keep one capture owner per review request and share the frozen context with delegates; repair drafts in the retained session and stop after terminal failures without automatic recapture.
- Record fetch attempt/success and requested/synced branch counts separately from branch entries and HTTP transport requests.
- Add an ancestor no-op coverage policy with explicit reviewer judgments for all seven dependency categories and supporting repositories; retain snapshots, raw patches, catalog entries and unverified environment status.
- Seal the captured base-to-head commit path and independently verify its hashes and parent links before granting ancestor exemptions, without adding historical trees or semantic review work.
- Add inclusive fixed-read ranges and saved search-ID filtering while preserving legacy output, batch errors, original indexes, complete evidence and search records.
- Document exact request and output shapes. This change validates helper regressions and makes no new model-quality benchmark claim; Context, Draft and Report remain v3.
