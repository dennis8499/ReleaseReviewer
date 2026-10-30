# Persistent release review sessions

Use the request facade for a new review. Legacy capture, evidence and report commands remain compatible with existing v3 contexts.

## Start and freeze

~~~powershell
python -X utf8 -B "<skill>/scripts/review_session.py" start --workspace "<workspace>" --project "<Repo path>" --base "<base branch>" --head "<head branch>"
~~~

Pass the exact Repo path and preserve the three user inputs. Use --workspace-file for a saved workspace. Keep request_id, runtime_root, context_dir, draft_path and files. The runtime and report directory must pass read/write/replace preflight and stay outside every Git Repo.

Read workspace instructions and fixed AGENTS.md evidence before semantic review. Complete capture for every available Repo before review begins. A local branch stays offline; explicit remote base/head refs are fetched once per remote after local preflight; supporting Repo HEADs are captured locally. Capture completion freezes every source. Later steps, validation, draft repair and retries use the same evidence and make no new network requests.

## Review and publish

Run the initial worklist, then save later operations as ordered UTF-8 JSON and continue with the same request:

~~~powershell
python -X utf8 -B "<skill>/scripts/review_session.py" step --runtime-root "<runtime>" --request-id "<id>" --worklist "<context>/review-worklist.json" --response-format auto
python -X utf8 -B "<skill>/scripts/review_session.py" check --runtime-root "<runtime>" --request-id "<id>"
python -X utf8 -B "<skill>/scripts/review_session.py" finish --runtime-root "<runtime>" --request-id "<id>"
~~~

Check aggregates structural diagnostics; it does not grant semantic approval. Finish validates the frozen evidence and saved searches, publishes one report, then cleans the owned context. A published status preserves its authoritative review outcome; publication success and review completeness are separate.

Use status to resume after interruption or conversation compaction. Exit 3 is repairable in the same request; exit 2 is terminal. Retry finish after a cleanup failure to recover the original report and Checklist. Cancel only when ending or abandoning the request.

For ordered-operation JSON, citation details, response paging, repair procedures and legacy behavior, load [session details](session-details.md) when needed.
