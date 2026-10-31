# Persistent release review sessions

Use the request facade for a new review. Legacy capture, evidence and report commands remain compatible with existing v3 contexts.

Read `capabilities` once and enable each optional feature only when its own `features` flag is true. If the command is unknown, retain the legacy `start --help` fallback. The Workbench prompt carries one request ID; retry with it, and use a new ID for resync. A prompt cannot grant host filesystem permissions, so stop after the helper's explicit runtime preflight diagnosis instead of probing repeatedly.

## Start and freeze

~~~powershell
python -X utf8 -B "<skill>/scripts/review_session.py" start --workspace "<workspace>" --project "<Repo path>" --base "<base branch>" --head "<head branch>" --request-id "<new request ID>"
~~~

Pass the exact Repo path and preserve the three user inputs. Workbench creates a fresh request ID when it copies a new prompt; keep that ID on retries. A requested resync uses a new ID. Use --workspace-file for a saved workspace. Keep request_id, runtime_root, context_dir, draft_path and files. The runtime and report directory must pass read/write/replace preflight and stay outside every Git Repo.

Read workspace instructions and fixed AGENTS.md evidence before semantic review. Complete capture for every available Repo before review begins. A local branch stays offline; explicit remote base/head refs are fetched once per remote after local preflight; supporting Repo HEADs are captured locally. Capture completion freezes every source. Later steps, validation, draft repair and retries use the same evidence and make no new network requests.

## Review and publish

Run the initial worklist, then save later operations as an ordered UTF-8 JSON array and continue with the same request. Keep even a single operation inside `[]`. Create the file and invoke step in one PowerShell call so large request bodies stay out of argv:

~~~powershell
python -X utf8 -B "<skill>/scripts/review_session.py" step --runtime-root "<runtime>" --request-id "<id>" --worklist "<context>/review-worklist.json" --response-format auto
python -X utf8 -B "<skill>/scripts/review_session.py" check --runtime-root "<runtime>" --request-id "<id>"
python -X utf8 -B "<skill>/scripts/review_session.py" finish --runtime-root "<runtime>" --request-id "<id>"
~~~

Check aggregates structural diagnostics; it does not grant semantic approval. Finish validates the frozen evidence and saved searches, publishes one report, then cleans the owned context. A published status preserves its authoritative review outcome; publication success and review completeness are separate.

If `features.read_window` is true, use `read-window` with `line_start` and `line_count` for bounded forward reading; it returns the actual final line, whole-file line count and next window, including an empty window beyond EOF. Otherwise keep using strict `read` with `line_start`/`line_end`. When `features.literal_search_batch` is true, consecutive case-sensitive literal searches with identical Repo/source/ref can share one Git grep call; each query still returns its own result and search ID. Case-insensitive searches keep their original per-query path.

When supported, pass `--updates-file <UTF-8 file>` to step, alone or with `--requests-file`. The `ReleaseReviewUpdates/v1` file binds `request_id`, `expected_revision`, `update_id`, `draft_updates` (JSON pointer/value pairs) and optional progress fields `read_evidence_ids`, `unread_cursor`, `excluded_questions`, `next_action`. The helper atomically saves explicit draft updates before running evidence operations. Same ID and same content are idempotent; changed content needs a new ID. Only explicit semantic judgments may set a draft item to `reviewed`. Operation checkpoints and semantic review progress remain separate.

Use `status --summary` to recover request phase, remaining work, draft revision and next action when that capability is declared. Use the standalone `view` command to read an artifact page without writing a checkpoint. The status summary and standalone view perform no Git calls or artifact writes. Older helpers keep the existing `status` and step `op="view"` path.

Use status to resume after interruption or conversation compaction. Exit 3 is repairable in the same request; exit 2 is terminal. Retry finish after a cleanup failure to recover the original report and Checklist. Cancel only when ending or abandoning the request.

For ordered-operation JSON, citation details, response paging, repair procedures and legacy behavior, load [session details](session-details.md) when needed.
