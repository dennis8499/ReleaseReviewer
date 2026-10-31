# Change log

## Unreleased

## 0.11.0 - 2026-11-01

- Fix summary pagination so the first omitted page is reachable; size artifact and string pages by serialized UTF-8 JSON bytes and keep sibling-item cursors visible.
- Record capture phases, Git command/output cost, fetch and network-lock time, pack/index time, snapshot validation, evidence reads, searches, publication verification, cleanup, and CLI output bytes. Keep unobservable Agent round trips, model tokens and HTTP/SSH requests `null`.
- Add `read-window`, hash-checked read-only `view`, bounded `status --summary`, revisioned idempotent draft updates and durable review progress. Preserve strict `read`, v3 artifacts, legacy CLI and same-request offline continuation.
- Batch compatible case-sensitive literal searches into one Git grep while preserving each query's result and search ID. Publication still independently replays the saved search ledger.
- Decode patch BOMs and explicit codecs without changing captured bytes; unknown bytes use reversible escapes. Add structured operation-field repair diagnostics and concise session examples.
- The supporting snapshot pack cache remains withheld until the isolated-cache tests and paired warm/cold performance gates can be measured. No end-to-end time, Agent-tool-call, or semantic-quality improvement is claimed by helper counters alone.

## 0.10.1 - 2026-10-30

- Reconcile current and historical documentation with the v0.10.0 source, configuration, CI workflow and retained artifacts; clarify archived attachment references and validation limits. This documentation-only patch does not change runtime behavior or data formats.

## 0.10.0 - 2026-10-30

- Batch frozen commit-to-tree resolution, `AGENTS.md` blob reads and publication citation reads while retaining per-source hash, path, line and search validation.
- Persist stage timing and operation counters; add bounded large-field artifact paging with JSON pointers, string offsets and continuation recipes under the 24 KiB response limit.
- Keep local reviews offline, explicit remote fetches scoped to one request and one operation per remote, and same-request continuation offline; preserve fetch reasons and separate Git command metrics from transport requests.
- Shorten the always-loaded skill and disclose Group, resource and recovery details by scenario. Keep Context, Draft and Report v3 and legacy response compatibility.
- Expand blind source-only fixtures for Table, FTP, SFTP, target changes, permissions and cross-Repo contract breaks; add a Codex matrix runner that retains redacted reports and leaves semantic scoring explicitly incomplete pending independent oracle review.
- Preserve HTTP/HTTPS URLs while redacting local Windows drive paths in benchmark artifacts; add regression coverage and restore the scorecard link.
- Publish the 0.10.0 Skill ZIP after the tag workflow validates the complete Windows/Python 3.10 and 3.14 matrix and canonical asset bytes. Helper performance targets pass; full paired Agent timing and three-model semantic scoring remain known acceptance limits. See the [release notes](docs/releases/v0.10.0.md) and [acceptance record](docs/optimization-candidate-v0.10.0.md).

## 0.9.0 - 2026-10-28

- Add ordered session worklist execution, stable operation IDs, per-repository batched blob reads and optional shared-content responses; preserve checkpoints, source identities, line ranges and the legacy response format.
- Reuse full snapshot validation only after checking a request-bound receipt against source descriptors and every owned snapshot file and directory. Retain full validation for older contexts and independent snapshot and search-ledger validation at publication.
- Resolve repository instructions from the fixed final entry tree or supporting HEAD; seed only necessary merge-parent reads while retaining historical sources for follow-up.
- Require new code risks to identify their fixed Diff roots and causal explanation, including verified final-path absence for deletion or rename evidence. Keep Context, Draft and Report at v3 and preserve legacy contexts.
- Record per-remote fetch reasons, phase and attempt/success counts; local branches and supporting repositories remain offline, and same-request retries do not refetch.
- Record the five-pair synthetic helper measurements, Windows/Python 3.14 regression and known acceptance limits in the [release notes](docs/releases/v0.9.0.md). Full Agent performance and three-model semantic quality remain unverified.

## 0.8.0 - 2026-10-23

- Publish with the explicitly accepted model-quality and performance limitations documented in [release notes](docs/releases/v0.8.0.md). The original acceptance result remains failed; release CI is a separate required gate.

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
