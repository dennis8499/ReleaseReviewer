# Persistent release review sessions

Use the request facade for new reviews. The original `git_review_context.py`, `evidence.py`, `release_report.py` and `cleanup --context-dir` commands remain compatible with existing v3 contexts.

## Start and resume

```powershell
python -X utf8 -B "<skill>/scripts/review_session.py" start --workspace "<group>" --project "<repo>" --base "main" --head "feature"
```

Supply `--workspace-file` for a declared saved workspace. Group requests default to `.release-review-runtime/` outside all repositories. A workspace that is itself a Git repository needs `--runtime-root` outside every Git root. Keep the returned `request_id`, `runtime_root`, `context_dir`, `draft_path` and `files` paths. An optional `--request-id` is 32 lowercase hexadecimal characters; repeating it with identical inputs resumes that request, while different inputs are rejected.

The runtime marker is `.release-reviewer-runtime.json`, schema `ReleaseReviewRuntime/v1`. Each `jobs/<id>.json` uses `ReleaseReviewSession/v1`. New contexts use an owned `release-reviewer-session/v2` marker under `sessions/<id>/`; legacy v1 sessions retain their system-temp boundary. This runtime belongs only to Release Reviewer. Each request fetches its explicit remote branches and seals its own snapshots. `--refresh` and `--auth-scope` are accepted for workbench routing; they do not enable a shared fetch or pack cache.

`network_activity` counts fetch Git commands and requested/synced refs. `network_log` records why sync was requested, its phase and each remote's attempted and successful fetch count, including failures. Local branches record `local-branches` and no fetches; HTTP and SSH request counts are `null` when not observed, not inferred from Git commands. Status and same-request retries retain the original log without another sync.

`start` prepares the unfinished `ReleaseReviewDraft/v3` draft and worklist, preserves all seven dependency categories, and freezes the entry and supporting identities. `status` returns the request state, original paths, checkpoints and metadata diagnostics after conversation compaction.

## Ordered rounds

Save a UTF-8 JSON array and invoke:

```powershell
python -X utf8 -B "<skill>/scripts/review_session.py" step --runtime-root "<runtime>" --request-id "<id>" --requests-file "<context>/round-1.json"
```

```json
[
  {"id":"entry-instructions","op":"instructions","repo":"entry"},
  {"id":"read-head","op":"read","repo":"entry","source":"head","path":"src/service.py","line_start":1,"line_end":20},
  {"id":"base-alias","op":"search","repo":"entry","source":"base","query":"INVOICE_API_URL"},
  {"id":"consumer-alias","op":"search","repo":"repo-001","source":"supporting","query":"InvoiceCreated"}
]
```

For the initial reads use `step --worklist <context>/review-worklist.json --response-format auto`. The helper derives stable operation IDs from each complete worklist request; repeating the same worklist reuses checkpoints. `--worklist` and `--requests-file` are mutually exclusive. The worklist retains all parent-view metadata, but initially reads only `required_merge_views`; other captured parents remain available for follow-up. For later rounds save the ordered operations above. Reads support inclusive line bounds; supporting sources use the fixed catalog Repo ID and `source="supporting"`. Merge-parent reads include its captured full `ref`. `list` returns fixed paths, `patch` accepts `diff.patch`, `release.patch` or `merge-output.txt`. `instructions` returns the entry's final clean merge-preview tree (or head if no preview) or the supporting Repo's captured HEAD; apply root and nested instructions only to their applicable paths. Workspace-ancestor instructions are separate frozen files; the mutable Repo worktree cannot replace fixed Repo instructions.

Each new context records the full start validation and binds its source description, directory inventory and every snapshot file hash to the request. Each round checks that receipt against the complete contents and structure of each used Repo, once per Repo, before reusing the prior structural validation. Added loose objects, changed controls, source descriptors, pack or index are terminal damage. Older contexts without the policy perform the original full validation. Successful IDs are checkpoints: identical input returns the saved artifact and does not append another logical result. A failed operation has no checkpoint and may be corrected under its original ID. `complete=false`, `next_index` and the valid prefix identify what remains. Changing an already successful ID's input is an error; use a new ID for new work.

A missing, unreadable or invalid UTF-8 JSON requests file returns exit 3 with `terminal=false`, `recoverable=true`, `context_retained=true` and `phase="input-repair"`. The job remains `reviewing`; create or correct that file and repeat `step` with the same request and operation IDs. Saved successful prefixes remain authoritative and are reused. These file-input diagnostics use owned metadata and execute zero Git commands. A damaged manifest or successful checkpoint artifact is a terminal owned-evidence error; it is never repaired by rereading the business repository.

Search computation may be reused across rounds inside one frozen context. Source identity and artifact checksums are still checked. Search records remain hash-addressed evidence, preserve the source/Repo/query identity, and are independently replayed at publication. Cache damage rebuilds the optional cache; owned snapshot damage terminates the request. Entry-base first-use searches and supporting-service searches remain different evidence.

`--response-format auto` selects the smaller response. In `ReleaseReviewStep/v2`, a full read retains its Repo, source, ref and path, and its `content_id` is the SHA-256 of its original blob bytes and points to `contents[content_id].lines`; different sources remain distinct evidence even when their lines are shared. Bounded reads retain their own line numbers. The default legacy response remains unchanged.

The CLI response is bounded to 24 KiB. If it returns an artifact, navigate the complete saved result with operations such as:

```json
[{"id":"page-1","op":"view","artifact":"<64-hex>","pointer":"/operations/0/result/lines","offset":0,"page_size":100}]
```

Use `next_offset` or `next_string_offset` until the necessary content is read. `evidence_complete` means the full result was saved, not that semantic review is complete. Pure saved-artifact views and draft checks execute zero Git commands.

For a shared response navigate `/contents/<content_id>/lines`; for a legacy response navigate `/operations/<index>/result/lines`. The saved artifact contains the entire result, even if the first response is only a summary.

## Check, finish and cancel

`check --runtime-root <runtime> --request-id <id>` aggregates draft shape and missing coverage errors using metadata. It returns `evidence_verified=false`; it cannot grant semantic approval or verify actual deployed conditions. Correct the draft, then call `finish` with the same runtime and ID.

The metadata check also enforces existing citation relationships: reviewed changed paths, final-entry evidence for risks and deployment dependencies, final configuration definitions, and each necessary merge-parent view's own paths and versions. It does not create graph nodes or infer new semantic relationships. A missing, unreadable or invalid UTF-8 JSON draft is a repairable `draft-repair` error; correct `draft_path` and retry the same request. Draft-file failures are checked before Git validation, while a readable draft still receives the full independent source and saved-search validation at `finish`.

New contexts use `risk_causality_policy=diff-causality/v1`. Each `code_risks` item needs `diff_roots` and a substantive `diff_reason`, with a citation to one selected root's before or after location. A deletion or rename may prove final absence with `final_absence`, for example `[{"repo":"entry","source":"merge-preview","ref":"<final tree SHA>","path":"removed/file.py"}]`, together with a cited old-version location and its deletion/rename Diff root. The fixed final tree must actually lack that path. This establishes evidence structure, not the causal or severity judgment; the agent must still explain why the missing behavior produces the claimed impact. Older contexts retain their original draft requirements.

`finish` keeps the full snapshot, citation, resource, chain and all-search-ledger validation. The fresh verification cache is separate from the reading cache. It records the report's static-body checksum outside the context before cleanup. Retry returns the same report after verifying that checksum; manual edits inside the Checklist are allowed. A cleanup failure keeps the publication receipt and never triggers another report. Static-body changes are reported without overwriting the report.

The same authoritative validation result is saved as `job.review_outcome` (`ReleaseReviewOutcome/v1`) together with the staged report receipt before the atomic report link. It preserves `review_complete`, `review_status`, `gaps`, the release assessment and deployment-pending counts after context cleanup. `publication_complete` describes publication only; an incomplete static review can still have a successfully published report. Supporting, coverage and chain gaps come from the validator, not from a new interpretation of the draft or Markdown.

Before publication, `check` tests draft structure: a valid unfinished draft can return exit 0 while `remaining` lists semantic work. After publication, `status` and `check` read the saved authoritative outcome without Git or a new semantic validation (`evidence_verified=false`, `evidence_validation="previous-published-validation"`). Published `check` returns exit 0 only for a known complete static review, and exit 3 for incomplete or unknown outcomes. A pending deployment Checklist does not make a complete static review incomplete. Human Checklist edits and cleanup retries preserve the original semantic outcome. Older receipts without a valid outcome return `outcome_available=false`, `review_complete=null`, `review_status="unknown"` and a nonempty availability explanation; they cannot claim a gap-free review.

Exit 0 means the operation succeeded, exit 3 indicates repairable input/draft errors in the same context, and exit 2 indicates a terminal error. `cancel` marks only that request, removes its owned context after the current local operation reaches a cancellation point, and leaves other requests and historical reports untouched. Use the same request's `status` to inspect the result. Existing `cleanup --context-dir` remains available for old sessions.
