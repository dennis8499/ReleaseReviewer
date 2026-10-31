"""Persistent, independently owned Release Reviewer request facade."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import time
import uuid

import review_coordinator as coordination
import review_runtime
import review_session

SCHEMA = "ReleaseReviewSession/v1"
LIMIT = 24 * 1024
VIEW_LIMIT = LIMIT - 1024
TERMINAL = {"published", "failed", "cancelled"}


def now():
    return datetime.now(timezone.utc).isoformat()


def resolve_runtime(workspace=None, explicit=None, create=False):
    value = explicit or os.environ.get("RELEASE_REVIEW_RUNTIME_DIR")
    if not value:
        if not workspace or (Path(workspace).resolve() / ".git").exists():
            raise ValueError("Use --runtime-root outside every Git Repo.")
        value = Path(workspace).resolve() / ".release-review-runtime"
    return coordination.runtime(value, create=create)


def job_path(root, identifier):
    if not isinstance(identifier, str) or not coordination.IDENTIFIER.fullmatch(identifier):
        raise ValueError("request_id must be 32 hexadecimal characters.")
    return root / "jobs" / (identifier + ".json")


def update(root, identifier, **fields):
    path = job_path(root, identifier)
    with coordination.FileLock(path.with_suffix(".state.lock")):
        job = coordination.read_json(path)
        job.update(fields, updated_at=now())
        coordination.atomic(path, job)
        return job


def add_metric(root, identifier, name, seconds):
    job = coordination.read_json(job_path(root, identifier))
    metrics = dict(job.get("metrics", {}))
    metrics[name] = metrics.get(name, 0.0) + max(0.0, float(seconds))
    return update(root, identifier, metrics=metrics)


def job_context(root, identifier, job):
    context = root / "sessions" / identifier
    if (job.get("schema") != SCHEMA or job.get("request_id") != identifier
            or job.get("runtime_root") != str(root)
            or job.get("context_dir") and Path(job["context_dir"]) != context):
        raise ValueError("Release request ownership differs.")
    return context


def metadata(root, identifier):
    job = coordination.read_json(job_path(root, identifier))
    context = job_context(root, identifier, job)
    return job, context


PROGRESS_SCHEMA = "ReleaseReviewProgress/v1"
UPDATES_SCHEMA = "ReleaseReviewUpdates/v1"
MAX_UPDATES_FILE_BYTES = 1024 * 1024
MAX_PROGRESS_BYTES = 256 * 1024
DRAFT_UPDATE_FIELDS = {"summary", "coverage", "merge_parent_review", "code_risks", "config_changes",
                       "db_changes", "external_changes", "dependency_coverage", "functional_chain",
                       "chain_gaps", "supporting_coverage", "next_steps", "tests_executed",
                       "tests_not_executed", "limitations"}


def progress_path(context):
    return Path(context) / "review-progress.json"


def empty_progress(identifier):
    return {"schema": PROGRESS_SCHEMA, "request_id": identifier, "revision": 0,
            "progress": {"read_evidence_ids": [], "unread_cursor": None,
                         "excluded_questions": [], "next_action": ""},
            "applied_updates": {}}


def read_progress(context, identifier):
    path = progress_path(context)
    if not path.exists():
        return None
    state = coordination.read_json(path)
    if (not isinstance(state, dict) or state.get("schema") != PROGRESS_SCHEMA
            or state.get("request_id") != identifier or type(state.get("revision")) is not int
            or state["revision"] < 0 or not isinstance(state.get("progress"), dict)
            or not isinstance(state.get("applied_updates", {}), dict)
            or len(review_runtime.canonical(state.get("progress", {}))) > MAX_PROGRESS_BYTES
            or len(state.get("applied_updates", {})) > 5000):
        raise ValueError("Owned review progress is invalid.")
    return state


def validate_progress_update(current, update):
    if not isinstance(update, dict):
        raise ValueError("/progress must be an object.")
    allowed = {"read_evidence_ids", "unread_cursor", "excluded_questions", "next_action"}
    if update.keys() - allowed:
        raise ValueError("/progress contains an unknown field; use read_evidence_ids, unread_cursor, excluded_questions, or next_action.")
    result = {**current, **update}
    for name in ("read_evidence_ids", "excluded_questions"):
        values = result.get(name)
        if (not isinstance(values, list) or len(values) > 5000
                or not all(isinstance(item, str) and len(item) <= 2048 for item in values)):
            raise ValueError(f"/progress/{name} must be a list of at most 5000 strings, each no longer than 2048 characters.")
    if not isinstance(result.get("next_action"), str) or len(result["next_action"]) > 4096:
        raise ValueError("/progress/next_action must be a string of at most 4096 characters.")
    cursor = result.get("unread_cursor")
    if cursor is not None and not isinstance(cursor, (str, dict, list)):
        raise ValueError("/progress/unread_cursor must be null, a string, an object, or an array.")
    if cursor is not None and len(review_runtime.canonical(cursor)) > 8192:
        raise ValueError("/progress/unread_cursor exceeds 8192 UTF-8 JSON bytes; save a smaller cursor.")
    if len(review_runtime.canonical(result)) > MAX_PROGRESS_BYTES:
        raise ValueError("/progress exceeds 256 KiB of UTF-8 JSON; keep only recent evidence IDs and concise questions.")
    return result


def set_draft_pointer(draft, pointer, value):
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise ValueError("/draft_updates/*/pointer must be a JSON pointer beginning with '/'.")
    raw_tokens = pointer[1:].split("/")
    if any(re.search(r"~(?![01])", token) for token in raw_tokens):
        raise ValueError(f"/draft_updates/*/pointer has an invalid '~' escape: {pointer}")
    tokens = [token.replace("~1", "/").replace("~0", "~") for token in raw_tokens]
    if not tokens or tokens[0] not in DRAFT_UPDATE_FIELDS:
        raise ValueError(f"/draft_updates/*/pointer must target an editable ReleaseReviewDraft field: {pointer}")
    target = draft
    for token in tokens[:-1]:
        if isinstance(target, list):
            if not token.isdigit() or int(token) >= len(target):
                raise ValueError(f"/draft_updates/*/pointer array index is outside the current draft: {pointer}")
            target = target[int(token)]
        elif isinstance(target, dict) and token in target:
            target = target[token]
        else:
            raise ValueError(f"/draft_updates/*/pointer does not exist in the current draft: {pointer}")
    final = tokens[-1]
    if isinstance(target, list):
        if not final.isdigit() or int(final) >= len(target):
            raise ValueError(f"/draft_updates/*/pointer array index is outside the current draft: {pointer}")
        target[int(final)] = value
    elif isinstance(target, dict) and final in target:
        target[final] = value
    else:
        raise ValueError(f"/draft_updates/*/pointer does not exist in the current draft: {pointer}")


def atomic_draft(path, draft):
    path = Path(path)
    if path.is_symlink():
        raise ValueError("Draft file cannot be a link.")
    temporary = path.with_name(".draft-update-" + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(draft, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def apply_updates_file(context, identifier, updates_file):
    """Atomically checkpoint explicit draft edits and semantic-review progress."""
    with review_runtime.writer(context):
        try:
            source = coordination.plain(Path(updates_file))
            if source.is_symlink() or not source.is_file():
                raise ValueError("updates file must be a regular local file")
            if source.stat().st_size > MAX_UPDATES_FILE_BYTES:
                raise ValueError("/updates_file exceeds the 1 MiB limit; keep draft updates small and write JSON to a file, not argv.")
            document = coordination.read_json(updates_file)
        except (OSError, ValueError) as exc:
            raise ValueError(f"/updates_file is not readable UTF-8 JSON: {coordination.safe_error(exc)}") from exc
        if not isinstance(document, dict) or document.get("schema") != UPDATES_SCHEMA:
            raise ValueError("/schema must be ReleaseReviewUpdates/v1.")
        allowed = {"schema", "request_id", "expected_revision", "update_id", "draft_updates", "progress"}
        if document.keys() - allowed:
            raise ValueError("/updates_file contains unknown fields: " + ", ".join(sorted(document.keys() - allowed)))
        if document.get("request_id") != identifier:
            raise ValueError("/request_id must match this session; do not mix updates across requests.")
        expected = document.get("expected_revision")
        if type(expected) is not int or expected < 0:
            raise ValueError("/expected_revision must be a nonnegative integer.")
        update_id = document.get("update_id")
        if not isinstance(update_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", update_id):
            raise ValueError("/update_id must contain 1 to 100 ASCII letters, digits, '_' or '-'.")
        patches = document.get("draft_updates", [])
        if (not isinstance(patches, list) or len(patches) > 5000
                or any(not isinstance(item, dict) or set(item) != {"pointer", "value"} for item in patches)):
            raise ValueError("/draft_updates must be an array of at most 5000 {pointer,value} objects.")
        update_digest = coordination.digest(document)
        state_path = progress_path(context)
        state = read_progress(context, identifier) or empty_progress(identifier)
        applied = state.setdefault("applied_updates", {})
        previous = applied.get(update_id)
        if previous is not None:
            if previous != update_digest:
                raise ValueError("/update_id was already used with different content; choose a new update_id.")
            return {"update_id": update_id, "revision": state["revision"], "applied": True, "replayed": True}
        if len(applied) >= 5000:
            raise ValueError("/update_id limit reached for this request; avoid creating redundant progress updates.")
        pending = state.get("pending_update")
        if pending:
            if pending.get("update_id") != update_id or pending.get("digest") != update_digest:
                raise ValueError(f"/update_id {pending.get('update_id')} has an incomplete atomic update; retry that exact file first.")
            if expected != state["revision"]:
                raise ValueError(f"/expected_revision is stale: expected {state['revision']} after pending recovery.")
        elif expected != state["revision"]:
            raise ValueError(f"/expected_revision conflict: current revision is {state['revision']}; reload status and create a new update.")

        draft_path = Path(context) / "review-result-draft.json"
        draft = coordination.read_json(draft_path)
        if not isinstance(draft, dict) or draft.get("schema") != "ReleaseReviewDraft/v3":
            raise ValueError("/draft_path must contain a ReleaseReviewDraft/v3 object before applying updates.")
        for index, item in enumerate(patches):
            if set(item) != {"pointer", "value"}:
                raise ValueError(f"/draft_updates/{index} must contain exactly pointer and value.")
            try:
                set_draft_pointer(draft, item["pointer"], item["value"])
            except ValueError as exc:
                raise ValueError(str(exc).replace("/draft_updates/*", f"/draft_updates/{index}")) from exc
        progress = validate_progress_update(state["progress"], document.get("progress", {}))
        if not pending:
            state["pending_update"] = {"update_id": update_id, "digest": update_digest,
                                        "draft_updates": patches, "progress": progress}
            coordination.atomic(state_path, state)
        atomic_draft(draft_path, draft)
        next_state = {"schema": PROGRESS_SCHEMA, "request_id": identifier,
                      "revision": state["revision"] + 1, "progress": progress,
                      "applied_updates": {**applied, update_id: update_digest}}
        coordination.atomic(state_path, next_state)
        return {"update_id": update_id, "revision": next_state["revision"],
                "applied": True, "replayed": False}


def preflight(path):
    path = coordination.plain(path)
    path.mkdir(parents=True, exist_ok=True)
    probe = path / (".release-probe-" + uuid.uuid4().hex)
    moved = probe.with_suffix(".checked")
    try:
        probe.write_bytes(b"review")
        if probe.read_bytes() != b"review":
            raise OSError("Release preflight read differs.")
        os.replace(probe, moved)
    finally:
        probe.unlink(missing_ok=True)
        moved.unlink(missing_ok=True)


def repo_index(manifest):
    return [{key: row[key] for key in ("id", "name", "path", "role", "status", "ref") if key in row}
            for row in manifest["repositories"]]


def fixed_identity(manifest):
    return {key: manifest[key] for key in ("base_input", "head_input", "base_sha", "head_sha", "merge_base",
            "diff_base", "generated_at", "capture_completed_at", "snapshot_policy") if key in manifest}


def receipt_anchors(job, manifest):
    import frozen_evidence
    policy = manifest.get("validation_policy")
    if policy is None:
        if "snapshot_receipts" in job:
            raise ValueError("Snapshot validation policy was removed from this request.")
        return None  # Sessions created by older versions always fully validate.
    if policy != frozen_evidence.RECEIPT_POLICY:
        raise ValueError("Unsupported snapshot validation policy.")
    if job.get("risk_causality_policy") != manifest.get("risk_causality_policy"):
        raise ValueError("Risk causality policy differs from this request.")
    anchors = job.get("snapshot_receipts")
    captured = {item["id"]: item for item in manifest["repositories"] if item["status"] == "captured"}
    if not isinstance(anchors, dict) or set(anchors) != set(captured):
        raise ValueError("Snapshot receipts do not match this request's captured repositories.")
    for identifier, record in captured.items():
        receipt = record.get("validation_receipt")
        if not isinstance(receipt, dict) or frozen_evidence.receipt_digest(receipt) != anchors[identifier]:
            raise ValueError("Snapshot validation receipt differs from this request.")
    return anchors


def diagnostics(draft, manifest):
    """Aggregate only draft/metadata checks; no Git and no semantic approval."""
    import review_worklist
    import evidence as ev
    import chain_review
    import coverage_policy
    errors = []
    def error(code, pointer, message):
        errors.append({"code": code, "pointer": pointer, "message": message})
    if not isinstance(draft, dict):
        error("draft_type", "", "draft must be an object")
        draft = {}
    if draft.get("schema") != "ReleaseReviewDraft/v3":
        error("schema", "/schema", "schema must be ReleaseReviewDraft/v3")
    policy = manifest.get("risk_causality_policy")
    if policy is not None and (policy != review_worklist.RISK_POLICY or draft.get("risk_causality_policy", policy) != policy):
        error("risk_policy", "/risk_causality_policy", "draft and fixed risk causality policy must match")
    if draft.get("report_layout") not in (None, "engineering-review/v1", "engineering-review/v2"):
        error("layout", "/report_layout", "unsupported report layout")
    if not isinstance(draft.get("summary"), str) or not draft["summary"].strip():
        error("required", "/summary", "summary is required")
    fields = ("coverage", "merge_parent_review", "code_risks", "config_changes", "db_changes",
              "external_changes", "dependency_coverage", "functional_chain", "chain_gaps",
              "supporting_coverage", "next_steps", "tests_executed", "tests_not_executed", "limitations")
    rows = {}
    for field in fields:
        value = draft.get(field, [] if field == "merge_parent_review" else None)
        if not isinstance(value, list):
            error("array", "/" + field, field + " must be an array")
            value = []
        rows[field] = value
    for field in ("chain_gaps", "next_steps", "tests_executed", "tests_not_executed", "limitations"):
        for i, value in enumerate(rows[field]):
            if not isinstance(value, str) or not value.strip():
                error("text", f"/{field}/{i}", "item must be nonempty text")
    catalog = {row["id"]: row for row in manifest["repositories"]}
    preview = manifest.get("merge_preview") or {}
    final_pair = (("merge-preview", preview["tree_sha"]) if preview.get("status") == "clean"
                  else ("head", manifest["head_sha"]))
    refs = {"base": manifest["base_sha"], "head": manifest["head_sha"],
            "merge-base": manifest.get("merge_base"), "merge-preview": preview.get("tree_sha")}
    parent_refs = {value for item in manifest["merge_commits"] for value in item["parents"]}
    noop = coverage_policy.ancestor_noop(manifest)

    def nonempty(value):
        return isinstance(value, str) and bool(value.strip())

    def final_evidence(items):
        return any(item["repo"] == "entry" and (item["source"], item["ref"]) == final_pair for item in items)

    def citations(value, pointer):
        checked = []
        if not isinstance(value, list):
            error("evidence_array", pointer, "evidence must be an array")
            return checked
        for i, item in enumerate(value):
            at = f"{pointer}/{i}"
            if not isinstance(item, dict):
                error("evidence_object", at, "evidence must be an object")
                continue
            repo, source, reference, path = (item.get(key) for key in ("repo", "source", "ref", "path"))
            valid = True
            if not isinstance(repo, str) or repo not in catalog:
                error("evidence_repo", at + "/repo", "evidence Repo must name a captured catalog identity")
                valid = False
            if not isinstance(source, str) or source not in (*refs, "merge-parent", "supporting"):
                error("evidence_source", at + "/source", "unknown fixed source")
                valid = False
            if not isinstance(reference, str) or not ev.SHA.fullmatch(reference):
                error("evidence_ref", at + "/ref", "full fixed SHA is required")
                valid = False
            if not ev.safe_path(path):
                error("evidence_path", at + "/path", "safe Repo-relative path is required")
                valid = False
            if valid:
                expected_ref = catalog[repo].get("ref") if source == "supporting" else refs.get(source)
                if ((repo == "entry") != (source != "supporting") or catalog[repo]["status"] != "captured"
                        or (source == "merge-parent" and reference not in parent_refs)
                        or (source != "merge-parent" and reference != expected_ref)):
                    error("evidence_identity", at, "Repo/source/ref differs from the frozen catalog")
                    valid = False
                snapshot = catalog[repo].get("snapshot", {}).get("sources", {}).get(reference, {})
                entries = snapshot.get("entries")
                if isinstance(entries, list) and not any(row.get("path") == path and row.get("kind") == "blob" for row in entries):
                    error("evidence_path", at + "/path", "path is not a captured blob at this source")
                    valid = False
            first, last = item.get("line_start"), item.get("line_end")
            if (first is not None or last is not None) and not (type(first) is int and type(last) is int and 1 <= first <= last):
                error("evidence_lines", at, "line_start/line_end must be a valid inclusive integer range")
                valid = False
            if valid:
                checked.append(item)
        return checked

    def search_records(value, pointer, repo, source):
        if not isinstance(value, list):
            error("search_array", pointer, "saved search IDs must be an array")
            return
        for i, identifier in enumerate(value):
            at = f"{pointer}/{i}"
            if not isinstance(identifier, str) or not coordination.DIGEST.fullmatch(identifier):
                error("search_id", at, "saved search ID must be 64 lowercase hexadecimal characters")
                continue
            try:
                directory = Path(manifest["context_dir"]) / "searches"
                path = directory / (identifier + ".json")
                if directory.is_symlink() or path.is_symlink():
                    raise ValueError("saved search cannot be a link")
                record = coordination.read_json(path)
                if not isinstance(record, dict):
                    raise ValueError("saved search must be an object")
                if hashlib.sha256(ev.canonical(record)).hexdigest() != identifier:
                    raise ValueError("saved search checksum differs")
                reference = manifest["base_sha"] if source == "base" else catalog[repo].get("ref")
                if (record.get("repo") != repo or record.get("source") != source
                        or record.get("ref") != reference or record.get("scope") != "repo"):
                    raise ValueError("saved search must use this exact Repo and fixed source")
            except (OSError, ValueError, KeyError, TypeError) as exc:
                error("search_record", at, str(exc))

    roots = {row["id"] for row in manifest.get("diff_roots", [])}
    def diff_links(row, pointer):
        if "diff_roots" not in manifest:
            return
        identifiers = row.get("diff_roots")
        if (not isinstance(identifiers, list) or not identifiers
                or any(not isinstance(value, str) or value not in roots for value in identifiers)
                or len(identifiers) != len(set(value for value in identifiers if isinstance(value, str)))):
            error("diff_roots", pointer + "/diff_roots", "extensions need unique captured Diff root IDs")
        if not nonempty(row.get("diff_reason")):
            error("diff_reason", pointer + "/diff_reason", "describe the causal relation to the captured Diff")
    expected = set(review_worklist.expected_coverage(manifest))
    covered = set()
    for i, row in enumerate(rows["coverage"]):
        if not isinstance(row, dict):
            error("object", f"/coverage/{i}", "coverage must contain objects")
            continue
        path = row.get("path")
        if not isinstance(path, str) or path not in expected or path in covered:
            error("coverage_identity", f"/coverage/{i}/path", "unknown or duplicate changed path")
        else:
            covered.add(path)
        if row.get("status") not in ("reviewed", "metadata-only", "not-reviewed"):
            error("status", f"/coverage/{i}/status", "invalid coverage status")
        if row.get("status") == "reviewed" and not row.get("evidence"):
            error("evidence", f"/coverage/{i}/evidence", "reviewed coverage needs fixed evidence")
        evidence = citations(row.get("evidence", []), f"/coverage/{i}/evidence")
        if any(item["repo"] != "entry" or item["path"] != path for item in evidence):
            error("coverage_evidence", f"/coverage/{i}/evidence", "coverage must cite its own entry path")
        if isinstance(path, str) and path in expected and row.get("status") == "reviewed":
            required = review_worklist.expected_coverage(manifest)[path]
            if not required <= {(item["source"], item["ref"]) for item in evidence}:
                error("coverage_sources", f"/coverage/{i}/evidence", "missing a required fixed comparison source")
    for path in sorted(expected - covered):
        error("missing_coverage", "/coverage", "missing changed path: " + path)
    kinds = {"config", "db", "ftp", "sftp", "redis", "kafka", "other"}
    actual = [row.get("kind") for row in rows["dependency_coverage"] if isinstance(row, dict)]
    for kind in sorted(kinds - set(k for k in actual if isinstance(k, str))):
        error("missing_dependency", "/dependency_coverage", "missing dependency category: " + kind)
    if len(actual) != len(set(k for k in actual if isinstance(k, str))) or any(not isinstance(k, str) or k not in kinds for k in actual):
        error("dependency_identity", "/dependency_coverage", "duplicate or unknown dependency category")
    for i, row in enumerate(rows["dependency_coverage"]):
        if not isinstance(row, dict):
            error("object", f"/dependency_coverage/{i}", "dependency coverage must contain objects")
            continue
        if row.get("status") not in ("reviewed", "not-reviewed", "not-applicable"):
            error("status", f"/dependency_coverage/{i}/status", "invalid dependency status")
        if not nonempty(row.get("reason")):
            error("required", f"/dependency_coverage/{i}/reason", "dependency disposition needs a reason")
        evidence = citations(row.get("evidence", []), f"/dependency_coverage/{i}/evidence")
        if row.get("status") == "reviewed" and expected and not final_evidence(evidence):
            error("dependency_final_source", f"/dependency_coverage/{i}/evidence", "reviewed dependency coverage needs final entry-version evidence")
        if row.get("status") == "not-applicable" and not noop:
            error("noop", f"/dependency_coverage/{i}/status", "not-applicable requires the derived ancestor no-op policy")
    peers = {row["id"] for row in manifest["repositories"] if row["role"] == "supporting"}
    actual_peers = [row.get("repo") for row in rows["supporting_coverage"] if isinstance(row, dict)]
    for repo in sorted(peers - set(r for r in actual_peers if isinstance(r, str))):
        error("missing_supporting", "/supporting_coverage", "missing supporting Repo: " + repo)
    if len(actual_peers) != len(set(r for r in actual_peers if isinstance(r, str))) or any(not isinstance(r, str) or r not in peers for r in actual_peers):
        error("supporting_identity", "/supporting_coverage", "duplicate or unknown supporting Repo")
    peer_status = {}
    for i, row in enumerate(rows["supporting_coverage"]):
        at = f"/supporting_coverage/{i}"
        if not isinstance(row, dict):
            error("object", at, "supporting coverage must contain objects")
            continue
        repo = row.get("repo")
        if not isinstance(repo, str) or repo not in peers:
            continue
        peer_status[repo] = row.get("status")
        if row.get("status") not in ("reviewed", "unrelated", "not-reviewed", "not-applicable"):
            error("status", at + "/status", "invalid supporting status")
        if not nonempty(row.get("reason")):
            error("required", at + "/reason", "supporting disposition needs a reason")
        evidence = citations(row.get("evidence", []), at + "/evidence")
        if any(item["repo"] != repo or item["source"] != "supporting" for item in evidence):
            error("supporting_evidence", at + "/evidence", "supporting coverage must cite its own fixed Repo")
        identifiers = row.get("searches", [])
        search_records(identifiers, at + "/searches", repo, "supporting")
        if row.get("status") in ("reviewed", "unrelated"):
            if catalog[repo]["status"] != "captured" or not isinstance(identifiers, list) or not identifiers:
                error("supporting_search", at + "/searches", "complete disposition needs saved whole-Repo searches")
            if row.get("status") == "reviewed" and not evidence:
                error("supporting_evidence", at + "/evidence", "reviewed supporting Repo needs content evidence")
        if row.get("status") == "not-applicable" and (not noop or evidence or identifiers):
            error("noop", at, "not-applicable requires ancestor no-op and empty evidence/searches")
    graph = {repo: set() for repo in catalog}
    for i, row in enumerate(rows["functional_chain"]):
        at = f"/functional_chain/{i}"
        if not isinstance(row, dict):
            error("object", at, "functional chain must contain objects")
            continue
        if row.get("id") != f"F-{i+1:03}":
            error("id", at + "/id", "functional chain IDs must be sequential")
        left, right = row.get("from_repo"), row.get("to_repo")
        valid_ends = all(isinstance(value, str) and value in catalog for value in (left, right))
        if not valid_ends:
            error("chain_endpoint", at, "functional chain endpoints must be captured Repo IDs")
        if not isinstance(row.get("kind"), str) or row["kind"] not in chain_review.KINDS:
            error("chain_kind", at + "/kind", "unknown functional chain kind")
        if row.get("status") not in ("reviewed", "not-reviewed"):
            error("status", at + "/status", "invalid functional chain status")
        if not nonempty(row.get("description")):
            error("required", at + "/description", "functional chain needs a concrete description")
        if row.get("status") == "not-reviewed" and not nonempty(row.get("reason")):
            error("required", at + "/reason", "unfinished functional chain needs a reason")
        if noop:
            error("noop", at, "ancestor no-op cannot add a functional chain")
        diff_links(row, at)
        evidence = citations(row.get("evidence", []), at + "/evidence")
        if valid_ends:
            if any(peer_status.get(value) == "unrelated" for value in (left, right) if value != "entry"):
                error("chain_disposition", at, "functional chain cannot cite an unrelated Repo")
            if row.get("status") == "reviewed":
                if not {left, right} <= {item["repo"] for item in evidence}:
                    error("chain_evidence", at + "/evidence", "reviewed chain needs both fixed endpoint identities")
                if "entry" in (left, right) and not any(item["repo"] == "entry" and (item["source"], item["ref"]) == final_pair for item in evidence):
                    error("chain_final_source", at + "/evidence", "entry endpoint needs final-version evidence")
                graph[left].add(right)
                graph[right].add(left)
    reached, pending = set(), ["entry"]
    while pending:
        repo = pending.pop()
        if repo not in reached:
            reached.add(repo)
            pending.extend(graph[repo] - reached)
    for repo, state in peer_status.items():
        if state == "reviewed" and repo not in reached:
            error("chain_disconnected", "/functional_chain", "reviewed supporting Repo is disconnected: " + repo)
    parent_views = {row["id"]: row for row in manifest.get("required_merge_views", [])}
    required_parents = set(parent_views)
    seen_parents = set()
    parents = {row.get("id") for row in rows["merge_parent_review"] if isinstance(row, dict) and isinstance(row.get("id"), str)}
    for i, row in enumerate(rows["merge_parent_review"]):
        at = f"/merge_parent_review/{i}"
        if not isinstance(row, dict):
            error("object", at, "merge parent coverage must contain objects")
            continue
        parent_id = row.get("id")
        if not isinstance(parent_id, str) or parent_id not in required_parents or parent_id in seen_parents:
            error("parent_id", at + "/id", "unknown required merge parent ID")
            view = None
        else:
            seen_parents.add(parent_id)
            view = parent_views[parent_id]
        if row.get("status") not in ("reviewed", "not-reviewed"):
            error("status", at + "/status", "invalid parent coverage status")
        if not nonempty(row.get("reason")):
            error("required", at + "/reason", "merge-parent coverage needs a final-version judgment")
        evidence = citations(row.get("evidence", []), at + "/evidence")
        if view is not None and row.get("status") == "reviewed":
            def parent_evidence(reference):
                return any(item["repo"] == "entry" and item["ref"] == reference and item["path"] in view["paths"] for item in evidence)
            if view.get("final_content_available", True):
                if not parent_evidence(final_pair[1]):
                    error("parent_final_evidence", at + "/evidence", "merge-parent review needs its own paths at the final fixed version")
            elif row.get("final_ref") != final_pair[1]:
                error("parent_final_ref", at + "/final_ref", "absence judgment needs the final fixed version")
            if view["parent_content_available"] and not parent_evidence(view["parent"]):
                error("parent_source_evidence", at + "/evidence", "merge-parent review needs its own paths at the fixed parent")
    for parent in sorted(required_parents - parents):
        error("missing_parent", "/merge_parent_review", "missing merge parent: " + parent)
    last_priority = -1
    reviewed_paths = {row["path"] for row in rows["coverage"] if isinstance(row, dict)
                      and isinstance(row.get("path"), str) and row.get("status") == "reviewed" and row["path"] in expected}
    for field, prefix, required in (
        ("code_risks", "R", ("title", "blocking_reason", "trigger", "impact", "recommendation", "scenario_source")),
        ("config_changes", "C", ("kind", "name", "description", "baseline_reason", "recommendation")),
        ("db_changes", "D", ("kind", "name", "description", "baseline_reason", "recommendation", "connection")),
        ("external_changes", "E", ("kind", "name", "description", "baseline_reason", "recommendation", "connection"))):
        for i, row in enumerate(rows[field]):
            if not isinstance(row, dict):
                error("object", f"/{field}/{i}", "item must be an object")
                continue
            if row.get("id") != f"{prefix}-{i+1:03}":
                error("id", f"/{field}/{i}/id", "IDs must be sequential")
            names = list(required)
            if field == "code_risks" and draft.get("report_layout"):
                names += ["scenario", "expected", "actual"]
            for name in names:
                value = row.get(name)
                if not isinstance(value, str) or not value.strip() or value.strip() == "未提供":
                    error("required", f"/{field}/{i}/{name}", name + " is required")
            if field == "code_risks":
                if manifest.get("risk_causality_policy") == review_worklist.RISK_POLICY:
                    diff_links(row, f"/{field}/{i}")
                if row.get("priority") not in ("P0", "P1"):
                    error("priority", f"/{field}/{i}/priority", "only P0/P1 release blockers are accepted")
                else:
                    priority = ("P0", "P1").index(row["priority"])
                    if priority < last_priority:
                        error("priority_order", f"/{field}/{i}/priority", "release risks must be ordered P0 then P1")
                    last_priority = priority
                if draft.get("report_layout"):
                    tech = row.get("technical_details", {})
                    for name in ("explanation", "impact_scope", "engineering_fix"):
                        value = tech.get(name) if isinstance(tech, dict) else None
                        if not isinstance(value, str) or not value.strip() or value.strip() == "未提供":
                            error("required", f"/{field}/{i}/technical_details/{name}", name + " is required")
            else:
                diff_links(row, f"/{field}/{i}")
                if not isinstance(row.get("baseline_searches"), list) or not row["baseline_searches"]:
                    error("baseline_search", f"/{field}/{i}/baseline_searches", "entry base first-use searches are required")
                search_records(row.get("baseline_searches", []), f"/{field}/{i}/baseline_searches", "entry", "base")
                if field == "config_changes":
                    definitions = citations(row.get("definition_evidence", []), f"/{field}/{i}/definition_evidence")
                    if row.get("definition_status") == "defined" and not final_evidence(definitions):
                        error("definition_final_source", f"/{field}/{i}/definition_evidence", "defined configuration needs final entry-version definition evidence")
            evidence = citations(row.get("evidence", []), f"/{field}/{i}/evidence")
            valid_absence = False
            if field == "code_risks" and manifest.get("risk_causality_policy") == review_worklist.RISK_POLICY:
                try:
                    _, absence = review_worklist.validate_risk_links(row, manifest, evidence)
                    valid_absence = bool(absence)
                except (ValueError, KeyError, TypeError) as exc:
                    error("risk_causality", f"/{field}/{i}", str(exc))
            if not final_evidence(evidence) and not valid_absence:
                error("risk_final_source" if field == "code_risks" else "extension_final_source", f"/{field}/{i}/evidence",
                      "item needs final entry-version behavior, use, target or definition evidence")
            if field == "code_risks":
                if not any(item["repo"] == "entry" and item["path"] in reviewed_paths for item in evidence):
                    error("risk_changed_path", f"/{field}/{i}/evidence", "risk must cite a reviewed changed entry path")
                if any(peer_status.get(item["repo"]) == "unrelated" for item in evidence):
                    error("risk_disposition", f"/{field}/{i}/evidence", "risk cannot cite an unrelated Repo")
    remaining = {
        "changes": sorted(expected - {row.get("path") for row in rows["coverage"] if isinstance(row, dict)
                                      and isinstance(row.get("path"), str) and row.get("status") == "reviewed"}),
        "parents": sorted(required_parents - {row.get("id") for row in rows["merge_parent_review"] if isinstance(row, dict)
                                              and isinstance(row.get("id"), str) and row.get("status") == "reviewed"}),
        "repositories": sorted(peers - {row.get("repo") for row in rows["supporting_coverage"] if isinstance(row, dict)
                                         and isinstance(row.get("repo"), str) and row.get("status") != "not-reviewed"}),
        "dependencies": [row.get("kind") for row in rows["dependency_coverage"] if isinstance(row, dict)
                         and row.get("status") == "not-reviewed"]}
    return {"schema": "ReleaseReviewDraftDiagnostics/v1", "evidence_verified": False,
            "git_commands": 0, "evidence_validation": "deferred-to-finish",
            "precheck_passed": not errors, "errors": errors, "remaining": remaining}


def check(root, identifier):
    import evidence
    job, context = metadata(root, identifier)
    if job["state"] == "published" or job.get("planned_report") and Path(job["planned_report"]).is_file():
        report_job = job if job["state"] == "published" else {**job, "report_path": job["planned_report"]}
        verify_receipt(report_job)
        outcome = published_outcome(job)
        return {"schema": "ReleaseReviewDraftDiagnostics/v1", "request_id": identifier,
                "state": job["state"], "publication_complete": job["state"] == "published", "draft_check": "not-applicable-published",
                "evidence_verified": False, "git_commands": 0,
                "evidence_validation": "previous-published-validation", "precheck_passed": True,
                "check_passed": outcome["review_complete"] is True,
                "errors": [], "review_outcome": outcome, **outcome_fields(outcome)}
    if job["state"] not in ("ready", "reviewing"):
        raise ValueError("Session is not ready for draft checks.")
    manifest = evidence.load_manifest(context, full=False)
    try:
        draft = coordination.read_json(context / "review-result-draft.json")
    except (OSError, ValueError) as exc:
        return {"schema": "ReleaseReviewDraftDiagnostics/v1", "request_id": identifier,
                "state": job["state"], "terminal": False, "recoverable": True, "context_retained": True,
                "evidence_verified": False, "git_commands": 0, "evidence_validation": "deferred-to-finish", "precheck_passed": False,
                "errors": [{"code": "authoring.invalid-draft-file", "pointer": "/draft_path", "message": coordination.safe_error(exc)}]}
    return {"request_id": identifier, **diagnostics(draft, manifest)}


def status(root, identifier):
    job, context = metadata(root, identifier)
    published = False
    if job["state"] == "published":
        verify_receipt(job)
        published = True
    elif job.get("planned_report") and Path(job["planned_report"]).exists():
        verify_receipt({**job, "report_path": job["planned_report"]})
        published = True
    result = {key: value for key, value in job.items() if key != "input_digest"}
    result["publication_complete"] = job["state"] == "published"
    if published:
        outcome = published_outcome(job)
        result.update(review_outcome=outcome, **outcome_fields(outcome))
    if job["state"] in ("ready", "reviewing") and context.exists():
        result["resume"] = check(root, identifier)
        manifest = coordination.read_json(context / "manifest.json")
        result["resume"].update(fixed_identity(manifest))
        result["resume"].update(selected_repo="entry", repositories=repo_index(manifest),
                                operations=manifest.get("operations", {}))
    return result


def status_summary(root, identifier):
    """A read-only, bounded projection of one request; no Git or artifact writes."""
    import evidence as ev
    job, context = metadata(root, identifier)
    if job["state"] == "published":
        verify_receipt(job)
    elif job.get("planned_report") and Path(job["planned_report"]).is_file():
        verify_receipt({**job, "report_path": job["planned_report"]})
    result = {"schema": "ReleaseReviewStatusSummary/v1", "request_id": identifier,
              "state": job["state"], "phase": job.get("phase"),
              "updated_at": job.get("updated_at"),
              "repository": job.get("inputs", {}).get("project"),
              "base": job.get("inputs", {}).get("base"),
              "head": job.get("inputs", {}).get("head"),
              "fixed_identity": job.get("fixed_identity"),
              "draft_path": job.get("draft_path"),
              "publication": {"complete": job.get("state") == "published",
                              "report_path": job.get("report_path"),
                              "review_outcome": published_outcome(job) if job.get("state") == "published" else None}}
    if context.exists() and job["state"] in ("ready", "reviewing", "validating"):
        manifest = ev.load_manifest(context, full=False)
        worklist = coordination.read_json(context / "review-worklist.json")
        operation_ids = {"worklist-" + coordination.digest({"op": request.get("action"),
                         **{key: value for key, value in request.items() if key != "action"}})
                         for request in worklist.get("requests", []) if isinstance(request, dict)}
        checkpoints = manifest.get("operations", {})
        completed = len(checkpoints)
        done_worklist = len(operation_ids.intersection(checkpoints))
        diagnostics_result = diagnostics(coordination.read_json(context / "review-result-draft.json"), manifest)
        state = read_progress(context, identifier)
        result["counts"] = {"completed_operations": completed,
                             "worklist_operations": len(operation_ids),
                             "worklist_completed": done_worklist,
                             "worklist_remaining": max(0, len(operation_ids) - done_worklist)}
        if state is not None:
            saved_progress = state["progress"]
            evidence_ids = saved_progress.get("read_evidence_ids", [])
            excluded = saved_progress.get("excluded_questions", [])
            result["review_progress"] = {
                "revision": state["revision"],
                "update_count": len(state.get("applied_updates", {})),
                "pending_update": bool(state.get("pending_update")),
                "read_evidence_count": len(evidence_ids),
                "read_evidence_ids": [item[:128] for item in evidence_ids[-16:]],
                "read_evidence_truncated": len(evidence_ids) > 16 or any(len(item) > 128 for item in evidence_ids[-16:]),
                "unread_cursor": saved_progress.get("unread_cursor"),
                "excluded_question_count": len(excluded),
                "excluded_questions": [item[:256] for item in excluded[-8:]],
                "excluded_questions_truncated": len(excluded) > 8 or any(len(item) > 256 for item in excluded[-8:]),
                "next_action": saved_progress.get("next_action", "")[:512]}
        else:
            result["review_progress"] = None
        result["draft_remaining"] = {name: len(values) if isinstance(values, list) else None
                                      for name, values in diagnostics_result.get("remaining", {}).items()}
        result["diagnostic_errors"] = len(diagnostics_result.get("errors", []))
    elif job.get("state") == "published":
        result["counts"] = {"completed_operations": None, "worklist_operations": None,
                             "worklist_completed": None, "worklist_remaining": 0}
        result["review_progress"] = None
    if len(review_runtime.canonical(result)) > VIEW_LIMIT:
        raise ValueError("Status summary exceeds the bounded response size.")
    return result


def view_page(root, identifier, artifact, pointer="", offset=0, page_size=100, string_offset=0):
    """Read and checksum one saved artifact page without writing session state."""
    _job, context = metadata(root, identifier)
    if not context.is_dir():
        raise ValueError("This request has no retained evidence context.")
    return artifact_view(context, artifact, pointer, offset, page_size, string_offset)


def installed_payload_identity():
    root = Path(__file__).resolve().parents[1]
    rows = []
    skip = {"__pycache__", ".pytest_cache", ".git"}
    for directory, names, files in os.walk(root, followlinks=False):
        names[:] = sorted(name for name in names if name not in skip)
        base = Path(directory)
        if any((base / name).is_symlink() for name in names):
            raise ValueError("Installed skill payload cannot contain symlink directories.")
        for name in files:
            path = base / name
            if name.endswith(".pyc"):
                continue
            if path.is_symlink() or not path.is_file():
                raise ValueError("Installed skill payload can contain only regular files.")
            relative = path.relative_to(root).as_posix()
            rows.append([relative, hashlib.sha256(path.read_bytes()).hexdigest()])
    rows.sort(key=lambda row: row[0].encode("utf-8"))
    payload_sha = hashlib.sha256(review_runtime.canonical(rows)).hexdigest()
    version = (root / "VERSION").read_text(encoding="utf-8").strip()
    return version, payload_sha, len(rows)


def capabilities(workspace=None, expected_payload_sha256=None):
    """Report helper support using read-only local inspection only."""
    version, payload_sha, files = installed_payload_identity()
    if expected_payload_sha256 is not None:
        if not re.fullmatch(r"[a-f0-9]{64}", expected_payload_sha256):
            raise ValueError("--expected-payload-sha256 must be a 64-character lowercase SHA-256.")
        if expected_payload_sha256 != payload_sha:
            raise ValueError("Installed ReleaseReviewer payload hash differs from Workbench binding.")
    runtime = os.environ.get("RELEASE_REVIEW_RUNTIME_DIR")
    if not runtime and workspace:
        runtime = str(Path(workspace).resolve() / ".release-review-runtime")
    return {"schema": "ReviewCapabilities/v1", "tool": "release-reviewer",
            "skill_version": version, "prepare_evidence": True,
            "updates_schema": UPDATES_SCHEMA, "shared_cache_schema": "ReviewerSharedCache/v2",
            "payload_sha256": payload_sha, "payload_files": files,
            "runtime_root": runtime or "", "git_commands": 0, "network_commands": 0,
            "features": {"request_id": True, "read_only_view": True, "status_summary": True,
                         "read_window": True, "updates_file": True,
                         "durable_review_progress": True, "phase_metrics": True,
                         "literal_search_batch": True, "patch_encoding": True,
                         "operation_diagnostics": True},
            "observability": {"agent_tool_round_trips": None, "model_tokens": None,
                              "http_requests": None, "ssh_requests": None}}


def start(args):
    import git_review_context as capture
    import git_support as git
    import group_context
    if args.expected_payload_sha256:
        capabilities(args.workspace, args.expected_payload_sha256)
    root = resolve_runtime(args.workspace, args.runtime_root, create=True)
    identifier = args.request_id or uuid.uuid4().hex
    args.request_id = identifier
    import frozen_evidence
    frozen_evidence.require_supported_root(root / "sessions" / identifier / "repos/entry/snapshot.git")
    path = job_path(root, identifier)
    inputs = {"workspace": str(Path(args.workspace).resolve()), "project": args.project,
              "base": args.base, "head": args.head, "workspace_file": args.workspace_file,
              "auth_scope": args.auth_scope, "refresh": args.refresh,
              "workspace_context": args.workspace_context_json}
    fingerprint = coordination.digest(inputs)
    owner = coordination.FileLock(path.with_suffix(".owner.lock"))
    if not owner.acquire(blocking=False):
        return status(root, identifier)
    try:
        if path.exists():
            old = coordination.read_json(path)
            if old.get("input_digest") != fingerprint:
                raise ValueError("request_id already belongs to different inputs.")
            return status(root, identifier)
        job = {"schema": SCHEMA, "tool": "release-reviewer", "request_id": identifier, "runtime_root": str(root),
               "state": "running", "phase": "preflight", "started_at": now(), "updated_at": now(),
               "inputs": inputs, "input_digest": fingerprint, "pid": os.getpid()}
        coordination.atomic(path, job)
        context = None
        active = coordination.Coordination(root, identifier, lambda phase, fields: update(root, identifier, phase=phase, **fields),
                                          args.refresh, args.auth_scope)
        began = time.perf_counter()
        capture_metrics = lock_metrics = None
        try:
            repositories, discovery_gaps = group_context.discover(Path(args.workspace).resolve(), Path(args.workspace_file) if args.workspace_file else None)
            repo = group_context.choose(repositories, Path(args.workspace).resolve(), args.project)
            if any(root.is_relative_to(item.resolve()) for item in repositories):
                raise ValueError("Release runtime must be outside every declared Git Repo.")
            preflight(root)
            preflight(repo / "review-reports")
            if any(git.remote_target_for_ref(repo, value) for value in (args.base, args.head)):
                preflight(coordination.git_network_lease.runtime_root())
            inputs["project"] = str(repo.resolve())
            update(root, identifier, inputs=inputs)
            context = review_session.create_session(runtime_root=root, request_id=identifier)
            update(root, identifier, context_dir=str(context), draft_path=str(context / "review-result-draft.json"))
            options = ["--workspace", args.workspace, "--project", args.project, "--base", args.base,
                       "--head", args.head, "--git-timeout", str(args.git_timeout)]
            if args.workspace_file:
                options += ["--workspace-file", args.workspace_file]
            if args.workspace_context_json:
                options += ["--workspace-context-json", json.dumps(args.workspace_context_json, ensure_ascii=False)]
            capture_args = capture.parse_args(options)
            capture_args._prepared_context = str(context)
            capture_args._prepared_discovery = (repositories, discovery_gaps, repo)
            with coordination.measure_lock_wait() as lock_metrics, git.measure_capture() as capture_metrics:
                with active.activate():
                    active.check()
                    manifest = capture.build_manifest(capture_args)
                    active.check()
            update(root, identifier, state="ready", phase="review", selected_repo="entry",
                   network_activity=manifest["network_activity"],
                   network_log=manifest["network_log"],
                   fixed_identity=fixed_identity(manifest),
                   risk_causality_policy=manifest["risk_causality_policy"],
                   snapshot_receipts={item["id"]: frozen_evidence.receipt_digest(item["validation_receipt"])
                                      for item in manifest["repositories"] if item["status"] == "captured"},
                   metrics={"capture_seconds": time.perf_counter() - began,
                            "capture_instrumented_seconds": capture_metrics["capture_instrumented_seconds"],
                            "capture_phase_seconds": dict(capture_metrics["phase_seconds"]),
                            "capture_git_commands": capture_metrics["git_commands"],
                            "capture_git_seconds": capture_metrics["git_seconds"],
                            "capture_git_output_bytes": capture_metrics["git_output_bytes"],
                            "fetch_seconds": capture_metrics["fetch_seconds"],
                            "network_lock_wait_seconds": capture_metrics["network_lock_wait_seconds"],
                            "pack_seconds": capture_metrics["pack_seconds"],
                            "index_seconds": capture_metrics["index_seconds"],
                            "snapshot_validation_seconds": capture_metrics["snapshot_validation_seconds"],
                            "manifest_lock_wait_seconds": lock_metrics["local_lock_wait_seconds"],
                            "request_owner_lock_wait_seconds": owner.wait_seconds,
                            "agent_tool_round_trips": None, "model_tokens": None})
            summary = capture.capture_summary_v2(manifest)
            return {"schema": "ReleaseReviewStart/v1", "tool": "release-reviewer", "request_id": identifier, "runtime_root": str(root),
                    "state": "ready", "context_dir": str(context), "draft_path": str(context / "review-result-draft.json"),
                    **fixed_identity(manifest),
                    "selected_repo": "entry", "entry_repo": "entry", "repositories": repo_index(manifest),
                    "required_coverage": coordination.read_json(context / "review-worklist.json")["required_coverage"],
                    "required_merge_views": manifest.get("required_merge_views", []),
                    "workspace_instructions": manifest.get("workspace_instructions", []),
                    "network_activity": manifest["network_activity"], "network_log": manifest["network_log"],
                    "files": summary["files"], "step": {"operations": ["read", "read-window", "search", "list", "instructions", "patch", "view"],
                    "requests_file": "UTF-8 JSON array, each operation needs unique id and op"}}
        except BaseException as exc:
            failure_metrics = {"capture_seconds": time.perf_counter() - began,
                               "agent_tool_round_trips": None, "model_tokens": None}
            if capture_metrics is not None:
                failure_metrics.update({"capture_instrumented_seconds": capture_metrics.get("capture_instrumented_seconds"),
                                        "capture_phase_seconds": dict(capture_metrics.get("phase_seconds", {})),
                                        "capture_git_commands": capture_metrics.get("git_commands", 0),
                                        "capture_git_seconds": capture_metrics.get("git_seconds", 0.0),
                                        "capture_git_output_bytes": capture_metrics.get("git_output_bytes", 0),
                                        "fetch_seconds": capture_metrics.get("fetch_seconds", 0.0),
                                        "network_lock_wait_seconds": capture_metrics.get("network_lock_wait_seconds", 0.0),
                                        "pack_seconds": capture_metrics.get("pack_seconds", 0.0),
                                        "index_seconds": capture_metrics.get("index_seconds", 0.0),
                                        "snapshot_validation_seconds": capture_metrics.get("snapshot_validation_seconds", 0.0)})
            if lock_metrics is not None:
                failure_metrics["manifest_lock_wait_seconds"] = lock_metrics["local_lock_wait_seconds"]
            failure_metrics["request_owner_lock_wait_seconds"] = owner.wait_seconds
            update(root, identifier, state="cancelled" if active.cancelled() else "failed", phase="terminal",
                   error=coordination.safe_error(exc), finished_at_epoch=time.time(), metrics=failure_metrics,
                   **{key: getattr(exc, key) for key in ("network_activity", "network_log") if hasattr(exc, key)})
            if context and context.exists():
                cleanup_started = time.perf_counter()
                review_session.cleanup_session(context)
                update(root, identifier, metrics={**failure_metrics,
                       "cleanup_seconds": time.perf_counter() - cleanup_started})
            raise
    finally:
        owner.release()


def patch_content(path, raw=None, encoding=None):
    """Segment a captured patch for display without changing or guessing its bytes."""
    import codecs
    raw = Path(path).read_bytes() if raw is None else raw
    digest = hashlib.sha256(raw).hexdigest()
    boms = ((codecs.BOM_UTF32_LE, "utf-32-le", "utf-32-le-bom"),
            (codecs.BOM_UTF32_BE, "utf-32-be", "utf-32-be-bom"),
            (codecs.BOM_UTF8, "utf-8", "utf-8-bom"),
            (codecs.BOM_UTF16_LE, "utf-16-le", "utf-16-le-bom"),
            (codecs.BOM_UTF16_BE, "utf-16-be", "utf-16-be-bom"))
    bom, codec, label = next(((prefix, selected, name) for prefix, selected, name in boms
                             if raw.startswith(prefix)), (b"", None, None))
    if encoding is not None and not isinstance(encoding, str):
        raise ValueError("patch encoding must be a codec name string.")
    if codec is not None and encoding:
        try:
            explicit = codecs.lookup(encoding).name.replace("_", "-").lower()
        except LookupError as exc:
            raise ValueError(f"Unknown explicit patch encoding: {encoding!r}.") from exc
        if explicit not in (codec, "utf-8-sig" if codec == "utf-8" else codec,
                            "utf-16" if codec.startswith("utf-16") else codec,
                            "utf-32" if codec.startswith("utf-32") else codec):
            raise ValueError(f"Explicit patch encoding {encoding!r} conflicts with the byte-order mark ({label}).")
    if codec is None:
        try:
            codec = codecs.lookup(encoding or "utf-8").name
        except LookupError as exc:
            raise ValueError(f"Unknown explicit patch encoding: {encoding!r}.") from exc
        if codec in ("utf-16", "utf-32"):
            raise ValueError("UTF-16/UTF-32 patch content without a byte-order mark needs an explicit LE or BE codec.")
        label = codec
    payload = raw[len(bom):]
    try:
        display = payload.decode(codec, errors="strict")
        escaped = False
        encoding_name = label
    except UnicodeDecodeError:
        # Preserve undecodable bytes visibly. Never fall back to a locale or
        # machine-default code page when no explicit encoding was supplied.
        display = raw.decode("utf-8", errors="backslashreplace")
        escaped = True
        encoding_name = "utf-8-byte-escaped"
        bom = b""
        codec = "utf-8"
        payload = raw

    pieces = []
    if escaped:
        current, current_start, file_index, part, cursor = bytearray(), 0, -1, "file-header", 0

        def flush_escaped(end):
            nonlocal current, current_start
            if current:
                pieces.append((current_start, end, max(0, file_index), part,
                               bytes(current).decode("utf-8", errors="backslashreplace")))
                current = bytearray()
            current_start = end

        for line in raw.splitlines(keepends=True):
            if line.startswith(b"diff --git "):
                flush_escaped(cursor)
                file_index += 1
                part = "file-header"
                current_start = 0 if file_index == 0 and not pieces else cursor
            elif line.startswith((b"@@ ", b"@@@ ")):
                flush_escaped(cursor)
                part = "hunk"
                current_start = 0 if not pieces and file_index < 0 else cursor
            current.extend(line)
            cursor += len(line)
        flush_escaped(cursor)
    elif display:
        lines = display.splitlines(keepends=True)
        encoded_lines = [line.encode(codec, errors="strict") for line in lines]
        # Stateful or non-round-tripping codecs still get an intact display;
        # segment them as one byte range rather than inventing offsets.
        if b"".join(encoded_lines) != payload:
            pieces = [(0, len(raw), 0, "file-header", display)]
        else:
            current, current_start, file_index, part = [], 0, -1, "file-header"
            cursor = len(bom)

            def flush(end):
                nonlocal current, current_start
                if current:
                    pieces.append((current_start, end, max(0, file_index), part, "".join(current)))
                    current = []
                current_start = end

            for line, line_bytes in zip(lines, encoded_lines):
                if line.startswith("diff --git "):
                    flush(cursor)
                    file_index += 1
                    part = "file-header"
                    current_start = 0 if file_index == 0 and not pieces else cursor
                elif line.startswith(("@@ ", "@@@ ")):
                    flush(cursor)
                    part = "hunk"
                    current_start = 0 if not pieces and file_index < 0 else cursor
                current.append(line)
                cursor += len(line_bytes)
            flush(cursor)
    elif raw:
        pieces = [(0, len(raw), 0, "file-header", "")]
    segments = [{"index": index, "file_index": file_index,
                 "kind": kind, "byte_start": start, "byte_end": end, "text": text}
                for index, (start, end, file_index, kind, text) in enumerate(pieces)]
    return {"path": Path(path).name, "raw_sha256": digest, "raw_byte_length": len(raw),
            "encoding": encoding_name, "display_has_byte_escapes": escaped,
            "text": display, "segments": segments}


def artifact_view(context, artifact, pointer="", offset=0, page_size=100, string_offset=0):
    value = review_runtime.load_view(context, artifact)
    if not isinstance(pointer, str) or pointer and not pointer.startswith("/"):
        raise ValueError("view pointer must be a JSON pointer")
    for token in pointer.split("/")[1:]:
        token = token.replace("~1", "/").replace("~0", "~")
        value = value[int(token)] if isinstance(value, list) else value[token]
    if type(offset) is not int or offset < 0 or type(page_size) is not int or not 1 <= page_size <= 1000:
        raise ValueError("invalid view page")
    def child_pointer(parent, child):
        token = str(child).replace("~", "~0").replace("/", "~1")
        return parent + "/" + token

    def continuation(target, *, start=0, text_start=None):
        result = {"op": "view", "artifact": artifact, "pointer": target,
                  "offset": start, "page_size": page_size}
        if text_start is not None:
            result["string_offset"] = text_start
        return result

    if isinstance(value, list):
        if offset > len(value):
            raise ValueError("list offset exceeds available items")
        items = value[offset:offset + page_size]
        while items:
            end = offset + len(items)
            response = {"artifact": artifact, "pointer": pointer, "items": items,
                        "total": len(value), "offset": offset,
                        "next_offset": end if end < len(value) else None,
                        "view_complete": offset == 0 and end >= len(value)}
            if response["next_offset"] is not None:
                response["continue"] = continuation(pointer, start=end)
            if len(review_runtime.canonical(response)) <= VIEW_LIMIT:
                return response
            if len(items) > 1:
                items = items[:max(1, len(items) // 2)]
                continue
            item = items[0]
            item_pointer = child_pointer(pointer, offset)
            if isinstance(item, dict) and item:
                keys = list(item)
                key = max(keys, key=lambda candidate: len(review_runtime.canonical(item[candidate])))
                target = child_pointer(item_pointer, key)
                visible_keys = keys[:32]
                response = {"artifact": artifact, "pointer": pointer,
                            "items": [{"index": offset, "fields": visible_keys,
                                       "total_fields": len(keys)}],
                            "total": len(value), "offset": offset, "next_offset": offset + 1,
                            "view_complete": False, "continue": continuation(target)}
                if len(keys) > len(visible_keys):
                    response["items"][0]["fields_continue"] = continuation(item_pointer, start=len(visible_keys))
                if offset + 1 < len(value):
                    response["next_page"] = continuation(pointer, start=offset + 1)
                while len(review_runtime.canonical(response)) > VIEW_LIMIT and response["items"][0]["fields"]:
                    response["items"][0]["fields"] = response["items"][0]["fields"][:
                        len(response["items"][0]["fields"]) // 2]
                    visible = len(response["items"][0]["fields"])
                    if visible < len(keys):
                        response["items"][0]["fields_continue"] = continuation(item_pointer, start=visible)
                    else:
                        response["items"][0].pop("fields_continue", None)
                if len(review_runtime.canonical(response)) <= VIEW_LIMIT:
                    return response
                raise ValueError("oversized item key cannot be represented as a JSON pointer")
            if isinstance(item, list):
                response = {"artifact": artifact, "pointer": pointer,
                            "items": [{"index": offset, "item_count": len(item)}],
                            "total": len(value), "offset": offset, "next_offset": offset + 1,
                            "view_complete": False, "continue": continuation(item_pointer)}
                if offset + 1 < len(value):
                    response["next_page"] = continuation(pointer, start=offset + 1)
                if len(review_runtime.canonical(response)) <= VIEW_LIMIT:
                    return response
                raise ValueError("oversized list pointer cannot be represented")
            if isinstance(item, str):
                page = artifact_view(context, artifact, item_pointer, string_offset=string_offset)
                response = {"artifact": artifact, "pointer": pointer,
                            "items": [{"index": offset, "text": page["text"],
                                       "string_offset": page["string_offset"],
                                       "next_string_offset": page["next_string_offset"]}],
                            "total": len(value), "offset": offset, "next_offset": offset + 1,
                            "view_complete": page["view_complete"] and offset == 0 and len(value) == 1}
                if page.get("continue"):
                    response["continue"] = page["continue"]
                if offset + 1 < len(value):
                    response["next_page"] = continuation(pointer, start=offset + 1)
                if len(review_runtime.canonical(response)) <= VIEW_LIMIT:
                    return response
                raise ValueError("oversized string page cannot be represented")
            response = {"artifact": artifact, "pointer": pointer,
                        "items": [{"index": offset, "value": item}], "total": len(value),
                        "offset": offset, "next_offset": None,
                        "view_complete": offset == 0 and len(value) == 1}
            if len(review_runtime.canonical(response)) <= VIEW_LIMIT:
                return response
            raise ValueError("oversized scalar item cannot be paged")
        return {"artifact": artifact, "pointer": pointer, "items": [], "total": len(value),
                "offset": offset, "next_offset": None, "view_complete": offset == 0 and not value}
    if isinstance(value, str):
        if type(string_offset) is not int or string_offset < 0 or string_offset > len(value):
            raise ValueError("invalid string offset")
        remaining = len(value) - string_offset

        def string_response(width):
            text = value[string_offset:string_offset + width]
            end = string_offset + len(text)
            response = {"artifact": artifact, "pointer": pointer, "text": text,
                        "string_offset": string_offset,
                        "next_string_offset": end if end < len(value) else None,
                        "view_complete": string_offset == 0 and end >= len(value)}
            if response["next_string_offset"] is not None:
                response["continue"] = continuation(pointer, text_start=end)
            return response

        # Find the largest Unicode prefix whose complete serialized UTF-8 JSON
        # response fits the protocol limit. Character count is not byte count
        # for Chinese text, emoji, or JSON-escaped control characters.
        low, high = 0, remaining
        while low < high:
            middle = (low + high + 1) // 2
            if len(review_runtime.canonical(string_response(middle))) <= VIEW_LIMIT:
                low = middle
            else:
                high = middle - 1
        if remaining == 0:
            return string_response(0)
        if low == 0:
            raise ValueError("string view could not be bounded")
        return string_response(low)
    if isinstance(value, dict):
        keys = list(value)
        if offset > len(keys):
            raise ValueError("object field offset exceeds available fields")
        response = {"artifact": artifact, "pointer": pointer, "value": value,
                    "fields": keys[:32]}
        if offset == 0 and len(review_runtime.canonical(response)) <= VIEW_LIMIT:
            return response
        fields = keys[offset:offset + page_size]
        while fields:
            end = offset + len(fields)
            response = {"artifact": artifact, "pointer": pointer, "fields": fields,
                        "total_fields": len(keys), "offset": offset,
                        "next_offset": end if end < len(keys) else None,
                        "view_complete": offset == 0 and end >= len(keys)}
            if response["next_offset"] is not None:
                response["continue"] = continuation(pointer, start=end)
            if len(review_runtime.canonical(response)) <= VIEW_LIMIT:
                return response
            if len(fields) > 1:
                fields = fields[:max(1, len(fields) // 2)]
                continue
            raise ValueError("oversized object key cannot be represented as a JSON pointer")
        return {"artifact": artifact, "pointer": pointer, "fields": [],
                "total_fields": len(keys), "offset": offset, "next_offset": None,
                "view_complete": offset == 0 and not keys}
    response = {"artifact": artifact, "pointer": pointer, "value": value,
                "fields": None}
    if len(review_runtime.canonical(response)) <= VIEW_LIMIT:
        return response
    return response


def compact(value, context):
    inline = {**value, "outcome_available": True}
    if len(review_runtime.canonical(inline)) < LIMIT:
        return inline
    artifact = review_runtime.store_view(context, value)
    preferred = ("operations", "results", "matches", "reads", "files", "contents")
    pointer = ""
    if isinstance(value, dict):
        candidates = [key for key in preferred if isinstance(value.get(key), (list, dict, str))]
        nonempty = [key for key in candidates if value[key]]
        selected = max(nonempty or candidates, key=lambda key: len(review_runtime.canonical(value[key])),
                       default=None)
        if selected is None and value:
            selected = max(value, key=lambda key: len(review_runtime.canonical(value[key])))
        if selected is not None:
            token = str(selected).replace("~", "~0").replace("/", "~1")
            pointer = "/" + token
    first_view = artifact_view(context, artifact, pointer, page_size=100)
    # The summary does not inline the first page. Its continuation therefore
    # starts at offset zero; copying first_view["continue"] here would silently
    # skip that page (and can lose a separate next_page branch).
    next_request = {"op": "view", "artifact": artifact, "pointer": pointer,
                    "offset": 0, "page_size": 100}
    summary = {"schema": "ReleaseReviewEvidenceSummary/v1", "artifact": artifact,
            "evidence_complete": True, "view_complete": False,
            **{key: value[key] for key in ("request_id", "runtime_root", "context_dir", "draft_path", "state", "complete", "next_index", "error") if key in value},
            "fields": list(value)[:48], "continue": next_request}
    if isinstance(value, dict) and len(value) > len(summary["fields"]):
        summary["fields_continue"] = {"op": "view", "artifact": artifact, "pointer": "",
                                      "offset": len(summary["fields"]), "page_size": 100}
    if first_view.get("next_page"):
        summary["next_page"] = first_view["next_page"]
    while len(review_runtime.canonical(summary)) > VIEW_LIMIT and summary["fields"]:
        summary["fields"] = summary["fields"][:len(summary["fields"]) // 2]
        if isinstance(value, dict) and len(summary["fields"]) < len(value):
            summary["fields_continue"] = {"op": "view", "artifact": artifact, "pointer": "",
                                          "offset": len(summary["fields"]), "page_size": 100}
    if len(review_runtime.canonical(summary)) > VIEW_LIMIT:
        summary.pop("error", None)  # The complete error remains in the artifact.
    if len(review_runtime.canonical(summary)) > VIEW_LIMIT:
        raise ValueError("artifact continuation does not fit the response limit")
    return summary


def worklist_operations(path):
    """Map unchanged worklist requests to repeatable, content-derived IDs."""
    import evidence as ev
    requests = ev.load_requests(str(path))
    result = []
    for request in requests:
        ev.require(isinstance(request, dict), "worklist request must be an object")
        operation = {"op": request.get("action"), **{k: v for k, v in request.items() if k != "action"}}
        operation["id"] = "worklist-" + coordination.digest(operation)
        result.append(operation)
    return result


def read_prefix(manifest, context, operations, start, repo_id):
    """Select one Repo's reads within the consecutive valid read prefix."""
    import evidence as ev
    requests, seen = [], {}
    for operation in operations[start:]:
        if not isinstance(operation, dict) or operation.get("op") not in ("read", "read-window"):
            break
        op_id = operation.get("id")
        args = {key: value for key, value in operation.items() if key not in ("id", "op")}
        if not isinstance(op_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", op_id):
            break
        old = manifest["operations"].get(op_id)
        fingerprint = coordination.digest(operation)
        if (old and old["fingerprint"] != fingerprint) or (op_id in seen and seen[op_id] != fingerprint):
            break
        seen[op_id] = fingerprint
        request = {"action": operation["op"], **args}
        selected_id = request.get("repo", "entry")
        if request.get("source") is None:
            request["source"] = "head" if selected_id == "entry" else "supporting"
        try:
            ev.require(not request.keys() - ev.REQUEST_FIELDS, "unknown request fields")
            ev.require(isinstance(selected_id, str) and bool(selected_id), "invalid repository")
            ev.require(ev.safe_path(request.get("path")), "invalid path")
            ev.require(type(request.get("ignore_case", False)) is bool, "invalid ignore_case")
            if request["action"] == "read-window":
                ev.require(type(request.get("line_start")) is int and request["line_start"] >= 1,
                           "read-window line_start must be an integer of at least 1")
                ev.require(type(request.get("line_count")) is int and 1 <= request["line_count"] <= 1000,
                           "read-window line_count must be between 1 and 1000")
                ev.require("line_end" not in request, "read-window uses line_count, not line_end")
            elif "line_start" in request or "line_end" in request:
                begin, end = request.get("line_start"), request.get("line_end")
                ev.require(type(begin) is int and type(end) is int and 1 <= begin <= end, "invalid line range")
            selected = ev.repository(manifest, selected_id)
            ev.require(selected.get("status") == "captured", "repository was not captured")
            if old:
                continue  # A checkpoint is read from its saved artifact, not Git.
            reference, _ = ev.source_version(manifest, context, request["source"], request.get("ref"), selected_id)
            state = ev._cache(manifest, context)
            index_key = (selected_id, reference)
            if index_key not in state["path_indexes"]:
                entries = selected["snapshot"]["sources"][reference]["entries"]
                state["path_indexes"][index_key] = {entry["path"]: entry for entry in entries}
            found = state["path_indexes"][index_key].get(request["path"])
            ev.require(found is not None and found["kind"] == "blob", "path is not a fixed blob")
        except ev.EVIDENCE_ERRORS:
            break
        if selected_id == repo_id:
            requests.append(request)
    return requests


def operation_diagnostic(index, operation, message):
    """Return a repairable, field-addressed explanation for one rejected op."""
    pointer = f"/operations/{index}"
    field = None
    operation = operation if isinstance(operation, dict) else {}
    text = message.lower()
    candidates = ("line_start", "line_end", "line_count", "string_offset", "page_size", "offset",
                  "ignore_case", "encoding", "pointer", "artifact", "source", "repo", "query", "path", "ref", "id", "op")
    field = next((name for name in candidates
                  if re.search(r"(?<![a-z0-9_])" + re.escape(name) + r"(?![a-z0-9_])", text)), None)
    unknown = re.search(r"unknown (?:operation )?field[: ]+['\"]?([a-z0-9_-]+)", text)
    if unknown:
        field = unknown.group(1)
    if field is None and ("unsupported step operation" in text or "unknown operation" in text):
        field = "op"
    if field:
        pointer += "/" + field
    correction = "Correct this field and retry the same request and operation ID; successful prefix operations remain saved."
    valid_range = None
    if field == "id":
        valid_range = "1-100 ASCII letters, digits, '_' or '-'"
        correction = "Use a unique operation ID matching [A-Za-z0-9_-]{1,100}."
    elif field == "line_start":
        valid_range = "integer >= 1"
        correction = "Use a 1-based line number; read-window also returns the next valid line."
    elif field == "line_count":
        valid_range = "integer 1..1000"
        correction = "Reduce the window to at most 1000 lines and retry with read-window."
    elif field == "line_end":
        valid_range = "integer line_start..total_lines, inclusive"
        correction = "Use read-window to get total_lines and a safe continuation, or lower line_end."
    elif field == "page_size":
        valid_range = "integer 1..1000"
        correction = "Choose a page_size from 1 through 1000."
    elif field in ("offset", "string_offset"):
        valid_range = "integer >= 0 and <= the selected list/string length"
        correction = "Follow the returned continuation cursor instead of estimating offsets."
    elif field == "encoding":
        correction = "Use a valid explicit codec name, or omit it to use a recognized BOM/UTF-8 with reversible byte escapes."
    elif field == "pointer":
        correction = "Copy the JSON pointer from this request's artifact and escape '~' as '~0' and '/' as '~1'."
    elif field == "artifact":
        correction = "Copy the 64-character artifact ID from a successful operation result."
    elif field == "source":
        valid_range = "a source listed by the captured catalog"
        correction = "Copy the source and fixed ref from this request's catalog or successful operation result."
    elif field == "repo":
        valid_range = "a Repo ID listed in this request's catalog"
        correction = "Copy the Repo ID from this request's catalog."
    elif field in ("path", "ref"):
        correction = "Copy the path/ref from this request's catalog or successful operation result; do not reconstruct a SHA."
    elif field == "op":
        valid_range = "read, read-window, search, list, instructions, patch or view"
        correction = "Choose a supported operation and keep the existing operation ID only if its input is unchanged."
    return {"code": "authoring.invalid-operation", "pointer": pointer,
            "message": message, "valid_range": valid_range, "correction": correction}


def shared_step(outcome, content_ids):
    """Keep operation identities separate while sharing repeated full read lines."""
    contents, operations = {}, []
    for row in outcome["operations"]:
        result = row.get("result")
        identifier = content_ids.get(row.get("id"))
        if (identifier is not None and isinstance(result, dict) and isinstance(result.get("lines"), list)
                and all(key in result for key in ("repo", "source", "ref", "path"))
                and "line_start" not in result):
            if identifier in contents and contents[identifier]["lines"] != result["lines"]:
                raise ValueError("Saved read content identity differs from its lines.")
            contents.setdefault(identifier, {"lines": result["lines"]})
            result = {**{key: value for key, value in result.items() if key != "lines"}, "content_id": identifier}
            row = {**row, "result": result}
        operations.append(row)
    return {**outcome, "schema": "ReleaseReviewStep/v2", "operations": operations, "contents": contents}


def step(root, identifier, operations=None, *, requests_file=None, worklist=None, updates_file=None, response_format="legacy"):
    import evidence as ev
    import frozen_evidence
    path = job_path(root, identifier)
    request_owner_lock = coordination.FileLock(path.with_suffix(".owner.lock"))
    with request_owner_lock:
        job, context = metadata(root, identifier)
        if job["state"] not in ("ready", "reviewing"):
            raise ValueError("Session is not ready for review.")
        active = coordination.Coordination(root, identifier)
        results, checked, changed, content_ids = [], set(), False, {}
        began = time.perf_counter()
        try:
            with coordination.measure_lock_wait() as lock_metrics, active.activate(), review_runtime.writer(context):
                manifest = ev.load_manifest(context, full=False)
                anchors = receipt_anchors(job, manifest)
                checkpoints = manifest.setdefault("operations", {})
                updates_result = None
                ev.require(isinstance(checkpoints, dict) and all(
                    isinstance(key, str) and isinstance(value, dict)
                    and isinstance(value.get("fingerprint"), str) and coordination.DIGEST.fullmatch(value["fingerprint"])
                    and isinstance(value.get("artifact"), str) and coordination.DIGEST.fullmatch(value["artifact"])
                    and ("content_sha256" not in value or isinstance(value["content_sha256"], str)
                         and coordination.DIGEST.fullmatch(value["content_sha256"]))
                    for key, value in checkpoints.items()), "Owned operation checkpoints are invalid.")
                def input_failure(message, code="authoring.invalid-requests-file", pointer="/requests_file"):
                    errors = [{"code": code, "pointer": pointer, "message": message}]
                    update(root, identifier, state="reviewing", phase="input-repair", errors=errors,
                           metrics={**job.get("metrics", {}),
                                    "request_owner_lock_wait_seconds": job.get("metrics", {}).get("request_owner_lock_wait_seconds", 0.0)
                                    + request_owner_lock.wait_seconds})
                    return {"request_id": identifier, "state": "reviewing", "phase": "input-repair",
                            "operations": [], "complete": False, "next_index": 0, "error": message, "errors": errors,
                            "terminal": False, "recoverable": True, "context_retained": True,
                            "evidence_verified": False, "git_commands": 0,
                            **({"draft_update": updates_result} if updates_result is not None else {})}
                if requests_file is not None and worklist is not None:
                    return input_failure("requests-file and worklist are mutually exclusive")
                if response_format not in ("legacy", "auto"):
                    return input_failure("invalid response format")
                if updates_file is not None:
                    try:
                        updates_result = apply_updates_file(context, identifier, updates_file)
                    except (OSError, ValueError) as exc:
                        message = coordination.safe_error(exc)
                        match = re.match(r"(/[^: ]+)", message)
                        pointer = match.group(1) if match else "/updates_file"
                        return input_failure(message, "authoring.invalid-updates-file", pointer)
                if requests_file is not None or worklist is not None:
                    try:
                        operations = worklist_operations(worklist) if worklist is not None else coordination.read_json(requests_file)
                    except (OSError, ValueError) as exc:
                        return input_failure(coordination.safe_error(exc))
                if operations is None and updates_file is not None:
                    operations = []
                if not isinstance(operations, list) or (not operations and worklist is None and updates_file is None):
                    return input_failure("operations must be a nonempty array unless updates-file is supplied", "authoring.invalid-operations", "")
                with ev.operation_cache(manifest, context, persistent=True) as operation_metrics:
                    operation_metrics["metrics"].update(evidence_read_seconds=0.0, search_seconds=0.0)
                    prefetched_repos = set()
                    prepared_searches, prepared_search_seconds = {}, {}
                    for index, operation in enumerate(operations):
                        active.check()
                        try:
                            if not isinstance(operation, dict):
                                raise ValueError("operation must be an object")
                            op_id = operation.get("id")
                            if not isinstance(op_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", op_id):
                                raise ValueError("operation requires a unique id")
                            fingerprint = coordination.digest(operation)
                            old = manifest["operations"].get(op_id)
                            if old:
                                if old["fingerprint"] != fingerprint:
                                    raise ValueError("operation id already has different input")
                            kind = operation.get("op")
                            args = {key: value for key, value in operation.items() if key not in ("id", "op")}
                            if kind not in ("read", "read-window", "search", "list", "instructions", "patch", "view"):
                                raise ValueError("unsupported step operation")
                            allowed_args = (set(ev.REQUEST_FIELDS) - {"action"} if kind in
                                            ("read", "read-window", "search", "list") else
                                            {"path", "repo", "encoding"} if kind == "patch" else
                                            {"repo"} if kind == "instructions" else
                                            {"artifact", "pointer", "offset", "page_size", "string_offset"})
                            unknown_args = sorted(set(args) - allowed_args)
                            if unknown_args:
                                raise ValueError(f"unknown operation field: {unknown_args[0]}")
                            if kind not in ("read", "read-window"):
                                prefetched_repos.clear()
                            repo_id = args.get("repo", "entry")
                            repo = ev.repository(manifest, repo_id)
                            if kind != "view" and repo.get("status") != "captured":
                                raise ValueError(repo.get("reason") or "Requested Repo was not captured.")
                        except (ValueError, KeyError, TypeError, OSError) as exc:
                            message = coordination.safe_error(exc)
                            outcome = {"operations": results, "complete": False, "next_index": index,
                                       "error": message,
                                       "errors": [operation_diagnostic(index, operation, message)]}
                            break
                        if old:
                            # A saved successful checkpoint is owned evidence,
                            # so damage is fatal rather than an authoring typo.
                            result = review_runtime.load_view(context, old["artifact"])
                            if "content_sha256" in old:
                                content_ids[op_id] = old["content_sha256"]
                            results.append({"id": op_id, "reused": True, "artifact": old["artifact"], "result": result})
                            continue
                        # Source damage is terminal. Validation is once per used
                        # Repo in this round, never for pure artifact navigation.
                        if kind != "view" and repo_id not in checked:
                            frozen_evidence.validate_for_step(context, repo, None if anchors is None else anchors[repo_id])
                            checked.add(repo_id)
                        if kind in ("read", "read-window") and repo_id not in prefetched_repos:
                            requests = read_prefix(manifest, context, operations, index, repo_id)
                            if requests:
                                read_started = time.perf_counter()
                                ev._prefetch_reads(manifest, context, requests)
                                operation_metrics["metrics"]["evidence_read_seconds"] += time.perf_counter() - read_started
                            prefetched_repos.add(repo_id)
                        try:
                            if kind in ("read", "read-window", "search", "list"):
                                if args.get("source") is None:
                                    args["source"] = "head" if repo_id == "entry" else "supporting"
                                if kind == "search" and index not in prepared_searches:
                                    query = args.get("query")
                                    ignore_case = args.get("ignore_case", False)
                                    batchable = (type(ignore_case) is bool and not ignore_case
                                                 and isinstance(query, str) and bool(query)
                                                 and "\0" not in query and "\n" not in query
                                                 and not (set(args) - {"repo", "source", "ref", "query", "ignore_case"}))
                                    batch_indexes, batch_queries = ([index], [query]) if batchable else ([], [])
                                    seen_ids = {op_id}
                                    byte_count = len(query.encode("utf-8")) + 3 if batchable else 8193
                                    expected_source = args["source"]
                                    for candidate_index in (range(index + 1, min(len(operations), index + 32)) if batchable else ()):
                                        candidate = operations[candidate_index]
                                        if not isinstance(candidate, dict) or candidate.get("op") != "search":
                                            break
                                        candidate_id = candidate.get("id")
                                        if (not isinstance(candidate_id, str)
                                                or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", candidate_id)
                                                or candidate_id in seen_ids or candidate_id in checkpoints):
                                            break
                                        candidate_args = {key: item for key, item in candidate.items()
                                                          if key not in ("id", "op")}
                                        candidate_repo = candidate_args.get("repo", "entry")
                                        candidate_source = candidate_args.get("source") or (
                                            "head" if candidate_repo == "entry" else "supporting")
                                        candidate_query = candidate_args.get("query")
                                        candidate_case = candidate_args.get("ignore_case", False)
                                        if (candidate_repo != repo_id or candidate_source != expected_source
                                                or candidate_args.get("ref") != args.get("ref")
                                                or type(candidate_case) is not bool or candidate_case
                                                or not isinstance(candidate_query, str) or not candidate_query
                                                or "\0" in candidate_query or "\n" in candidate_query
                                                or set(candidate_args) - {"repo", "source", "ref", "query", "ignore_case"}):
                                            break
                                        byte_count += len(candidate_query.encode("utf-8")) + 3
                                        if byte_count > 8192:
                                            break
                                        seen_ids.add(candidate_id)
                                        batch_indexes.append(candidate_index)
                                        batch_queries.append(candidate_query)
                                    if len(batch_indexes) > 1:
                                        batch_started = time.perf_counter()
                                        batch_results = ev.search_many(manifest, context, expected_source,
                                                                      batch_queries, ref=args.get("ref"),
                                                                      repo=repo_id)
                                        prepared_searches.update(zip(batch_indexes, batch_results))
                                        prepared_search_seconds[batch_indexes[0]] = time.perf_counter() - batch_started
                                if kind == "search" and index in prepared_searches:
                                    result = prepared_searches[index]
                                    result["search_id"] = ev.record_search(context, result)
                                    operation_metrics["metrics"]["search_seconds"] += prepared_search_seconds.get(index, 0.0)
                                else:
                                    action_started = time.perf_counter()
                                    result = ev.request_result(manifest, context, {"action": kind, **args})
                                    elapsed = time.perf_counter() - action_started
                                    if kind == "search":
                                        operation_metrics["metrics"]["search_seconds"] += elapsed
                                    elif kind in ("read", "read-window"):
                                        operation_metrics["metrics"]["evidence_read_seconds"] += elapsed
                            elif kind == "view":
                                result = artifact_view(context, **args)
                            elif kind == "patch":
                                if set(args) - {"path", "repo", "encoding"} or args.get("path") not in ("diff.patch", "release.patch", "merge-output.txt"):
                                    raise ValueError("patch must name a captured patch and may include an explicit encoding")
                                result = patch_content(context / args["path"], encoding=args.get("encoding"))
                            else:
                                if set(args) - {"repo"}:
                                    raise ValueError("unknown instructions argument")
                                source = ("merge-preview" if manifest.get("merge_preview", {}).get("status") == "clean"
                                          else "head") if repo_id == "entry" else "supporting"
                                ref, _ = ev.source_version(manifest, context, source, repo=repo_id)
                                result = {"instructions": [{**item, "content": ev.read_blob(manifest, context, source, item["path"], repo=repo_id).decode("utf-8")}
                                                          for item in repo.get("instructions", []) if item["ref"] == ref]}
                            content_sha256 = None
                            if kind == "read" and "line_start" not in result:
                                raw = ev._read_blob(manifest, context, args["source"], args.get("path"),
                                                    args.get("ref"), args.get("repo"))[2]
                                content_sha256 = hashlib.sha256(raw).hexdigest()
                            artifact = review_runtime.store_view(context, result)
                            manifest["operations"][op_id] = {"fingerprint": fingerprint, "artifact": artifact}
                            if content_sha256 is not None:
                                content_ids[op_id] = content_sha256
                                manifest["operations"][op_id]["content_sha256"] = content_sha256
                            changed = True
                            results.append({"id": op_id, "artifact": artifact, "result": result})
                        except ev.EVIDENCE_ERRORS as exc:
                            message = coordination.safe_error(exc)
                            outcome = {"operations": results, "complete": False, "next_index": index,
                                       "error": message,
                                       "errors": [operation_diagnostic(index, operation, message)]}
                            break
                    else:
                        outcome = {"operations": results, "complete": True, "next_index": len(operations)}
                    cache = ev._cache(manifest, context)
                    outcome["search_activity"] = {"executed": cache["search_runs"], "cache_hits": cache["search_hits"]}
                    operation_metrics["metrics"].update(
                        searches_executed=cache["search_runs"], search_cache_hits=cache["search_hits"],
                        unique_blobs=len(cache["blobs"]), unique_citation_files=len(cache["metadata"]))
                if changed:
                    review_runtime.save_manifest(context, manifest)
            step_metrics = operation_metrics["metrics"]
            update(root, identifier, state="reviewing", phase="review", last_step={key: outcome[key] for key in ("complete", "next_index")},
                   errors=[],
                   metrics={**job.get("metrics", {}),
                            "read_search_seconds": job.get("metrics", {}).get("read_search_seconds", 0) + time.perf_counter() - began,
                            "step_operation_requests": job.get("metrics", {}).get("step_operation_requests", 0) + len(operations),
                            "step_operations": job.get("metrics", {}).get("step_operations", 0) + sum(not item.get("reused", False) for item in results),
                            "step_reused_operations": job.get("metrics", {}).get("step_reused_operations", 0) + sum(item.get("reused", False) for item in results),
                            "step_git_commands": job.get("metrics", {}).get("step_git_commands", 0) + step_metrics["git_commands"],
                            "step_git_seconds": job.get("metrics", {}).get("step_git_seconds", 0.0) + step_metrics["git_seconds"],
                            "step_output_bytes": job.get("metrics", {}).get("step_output_bytes", 0) + step_metrics["git_output_bytes"],
                            "step_git_output_bytes": job.get("metrics", {}).get("step_git_output_bytes", 0) + step_metrics["git_output_bytes"],
                            "step_evidence_read_seconds": job.get("metrics", {}).get("step_evidence_read_seconds", 0.0) + step_metrics["evidence_read_seconds"],
                            "step_search_seconds": job.get("metrics", {}).get("step_search_seconds", 0.0) + step_metrics["search_seconds"],
                            "manifest_lock_wait_seconds": job.get("metrics", {}).get("manifest_lock_wait_seconds", 0.0) + lock_metrics["local_lock_wait_seconds"],
                            "request_owner_lock_wait_seconds": job.get("metrics", {}).get("request_owner_lock_wait_seconds", 0.0) + request_owner_lock.wait_seconds,
                            "step_agent_tool_round_trips": None, "step_model_tokens": None,
                            "step_batched_blobs": job.get("metrics", {}).get("step_batched_blobs", 0) + step_metrics["batched_blobs"],
                            "step_searches": job.get("metrics", {}).get("step_searches", 0) + step_metrics["searches_executed"],
                            "step_search_cache_hits": job.get("metrics", {}).get("step_search_cache_hits", 0) + step_metrics["search_cache_hits"]})
            result = {"request_id": identifier, "state": "reviewing", "phase": "review", "terminal": False,
                      "recoverable": not outcome["complete"], "context_retained": True,
                      **({"draft_update": updates_result} if updates_result is not None else {}), **outcome}
            if response_format == "auto":
                shared = shared_step(result, content_ids)
                if len(review_runtime.canonical(shared)) < len(review_runtime.canonical(result)):
                    return shared
            return result
        except BaseException as exc:
            update(root, identifier, state="cancelled" if active.cancelled() else "failed", phase="terminal",
                   error=coordination.safe_error(exc), finished_at_epoch=time.time())
            cleanup_started = time.perf_counter()
            review_session.cleanup_session(context)
            add_metric(root, identifier, "cleanup_seconds", time.perf_counter() - cleanup_started)
            raise


def verify_receipt(job):
    import release_report
    report = coordination.plain(Path(job["report_path"]))
    project = Path(job["inputs"]["project"])
    expected = coordination.plain(project / "review-reports")
    if (not report.is_absolute() or not project.is_absolute() or report.parent != expected
            or expected.resolve() != expected or not report.is_file()):
        raise ValueError("Published report must exist in this request's reviewed Repo/review-reports.")
    body = report.read_text(encoding="utf-8")
    checksum = hashlib.sha256(release_report.static_body(body).encode("utf-8")).hexdigest()
    if checksum != job["report_static_sha256"]:
        raise ValueError("Published report static content changed; report was not replaced.")


def published_outcome(job):
    value = job.get("review_outcome")
    if (isinstance(value, dict) and value.get("schema") == "ReleaseReviewOutcome/v1"
            and type(value.get("review_complete")) is bool
            and isinstance(value.get("review_status"), str)
            and isinstance(value.get("gaps"), list)
            and all(isinstance(gap, str) for gap in value["gaps"])):
        return value
    return {"schema": "ReleaseReviewOutcome/v1", "outcome_available": False,
            "review_complete": None, "review_status": "unknown",
            "gaps": ["發布收據未保存有效的語意審查摘要；既有報告的完整性與缺口未知。"]}


def outcome_fields(outcome):
    return {key: outcome[key] for key in ("outcome_available", "review_complete", "review_status", "gaps", "remaining")
            if key in outcome}


def clean_published(root, identifier, context):
    cleanup_started = time.perf_counter()
    try:
        if context.exists():
            review_session.cleanup_session(context)
    except (OSError, ValueError) as exc:
        update(root, identifier, cleanup_pending=True, cleanup_error=coordination.safe_error(exc))
    else:
        update(root, identifier, cleanup_pending=False, cleanup_error=None)
    latest = coordination.read_json(job_path(root, identifier))
    update(root, identifier, metrics={**latest.get("metrics", {}),
                                     "cleanup_seconds": latest.get("metrics", {}).get("cleanup_seconds", 0.0)
                                     + time.perf_counter() - cleanup_started})
    return status(root, identifier), 0


def finish(root, identifier):
    import release_report
    path = job_path(root, identifier)
    with coordination.FileLock(path.with_suffix(".owner.lock")):
        job, context = metadata(root, identifier)
        if job["state"] == "published":
            verify_receipt(job)
            return clean_published(root, identifier, context)
        if job.get("planned_report") and Path(job["planned_report"]).is_file():
            verify_receipt({**job, "report_path": job["planned_report"]})
            update(root, identifier, state="published", phase="complete", report_path=job["planned_report"], finished_at_epoch=time.time())
            return clean_published(root, identifier, context)
        if job["state"] not in ("ready", "reviewing", "validating"):
            raise ValueError("Session cannot be published.")
        active = coordination.Coordination(root, identifier)
        validated_outcome = None
        validation_metrics = {}
        def reviewed(outcome):
            nonlocal validated_outcome
            validated_outcome = outcome
        def measured(metrics):
            validation_metrics.update(metrics)
        def staged(output, body):
            active.check()
            if validated_outcome is None:
                raise ValueError("Publication requires its authoritative validated review outcome.")
            update(root, identifier, planned_report=str(output),
                   report_static_sha256=hashlib.sha256(release_report.static_body(body).encode("utf-8")).hexdigest(),
                   review_outcome=validated_outcome)
        def published(output):
            update(root, identifier, state="published", phase="complete", report_path=str(output), finished_at_epoch=time.time())
        began = time.perf_counter()
        try:
            active.check()
            import evidence
            frozen = evidence.load_manifest(context, full=False)
            receipt_anchors(job, frozen)
            if Path(frozen["repo"]) != Path(job["inputs"]["project"]):
                raise ValueError("Captured entry Repo differs from this request's reviewed Repo.")
            update(root, identifier, state="validating", phase="validation")
            with active.activate():
                report = release_report.publish_report(context, context / "review-result-draft.json",
                    retain_on_validation_error=True, on_stage=staged, on_published=published,
                    on_review_outcome=reviewed, on_validation_metrics=measured, cleanup_context=False)
            update(root, identifier, metrics={**job.get("metrics", {}), **validation_metrics,
                                             "validation_publish_seconds": time.perf_counter() - began,
                                             "publish_verification_seconds": validation_metrics.get("validation_seconds", 0.0)})
            return clean_published(root, identifier, context)
        except release_report.DraftValidationError as exc:
            update(root, identifier, state="reviewing", phase="draft-repair", errors=[str(exc)],
                   metrics={**job.get("metrics", {}), **validation_metrics,
                            "validation_publish_seconds": time.perf_counter() - began,
                                             "publish_verification_seconds": validation_metrics.get("validation_seconds", 0.0)})
            result = check(root, identifier)
            if not result["errors"]:
                result["errors"] = [{"code": "full_validation", "pointer": "", "message": str(exc)}]
            return {**result, "state": "reviewing", "phase": "draft-repair", "terminal": False,
                    "recoverable": True, "context_retained": True}, 3
        except BaseException as exc:
            if coordination.read_json(path)["state"] == "published":
                raise
            update(root, identifier, state="cancelled" if active.cancelled() else "failed", phase="terminal",
                   error=coordination.safe_error(exc), finished_at_epoch=time.time(),
                   metrics={**job.get("metrics", {}), **validation_metrics,
                            "validation_publish_seconds": time.perf_counter() - began,
                                             "publish_verification_seconds": validation_metrics.get("validation_seconds", 0.0)})
            if context.exists():
                cleanup_started = time.perf_counter()
                review_session.cleanup_session(context)
                latest = coordination.read_json(path)
                update(root, identifier, metrics={**latest.get("metrics", {}),
                       "cleanup_seconds": latest.get("metrics", {}).get("cleanup_seconds", 0.0)
                       + time.perf_counter() - cleanup_started})
            raise


def cancel(root, identifier):
    job, context = metadata(root, identifier)
    if job["state"] in TERMINAL:
        return status(root, identifier)
    coordination.atomic(root / "jobs" / (identifier + ".cancel"), {"requested_at": now()})
    lock = coordination.FileLock(job_path(root, identifier).with_suffix(".owner.lock"))
    if lock.acquire(blocking=False):
        try:
            job, context = metadata(root, identifier)
            if job["state"] not in TERMINAL:
                if context.exists():
                    cleanup_started = time.perf_counter()
                    review_session.cleanup_session(context)
                    add_metric(root, identifier, "cleanup_seconds", time.perf_counter() - cleanup_started)
                update(root, identifier, state="cancelled", phase="terminal", finished_at_epoch=time.time())
        finally:
            lock.release()
    return status(root, identifier)


def parser():
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("command", choices=("start", "step", "check", "finish", "status", "view", "capabilities", "cancel"))
    for name in ("workspace", "workspace-file", "runtime-root", "request-id", "project", "base", "head"):
        value.add_argument("--" + name)
    value.add_argument("--requests-file", type=Path)
    value.add_argument("--worklist", type=Path)
    value.add_argument("--response-format", choices=("legacy", "auto"), default="legacy")
    value.add_argument("--updates-file", type=Path)
    value.add_argument("--summary", action="store_true")
    value.add_argument("--artifact")
    value.add_argument("--pointer", default="")
    value.add_argument("--offset", type=int, default=0)
    value.add_argument("--page-size", type=int, default=100)
    value.add_argument("--string-offset", type=int, default=0)
    value.add_argument("--expected-payload-sha256")
    value.add_argument("--workspace-context-json", type=json.loads, default={})
    value.add_argument("--refresh", action="store_true")
    value.add_argument("--prepare-evidence", action="store_true",
                       help="Prepare the frozen evidence catalog and review worklist during start.")
    value.add_argument("--auth-scope", default=os.environ.get("GITLAB_WORKSPACE_AUTH_SCOPE", ""))
    value.add_argument("--git-timeout", type=float, default=180)
    return value


def main(argv=None):
    for stream in (__import__("sys").stdout, __import__("sys").stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = parser().parse_args(argv)
    code = 0
    try:
        if args.command == "capabilities":
            result = capabilities(args.workspace, args.expected_payload_sha256)
            context = None
        elif args.command == "start":
            if not all((args.workspace, args.project, args.base, args.head)):
                raise ValueError("start requires workspace, project, base and head")
            result = start(args)
            context = Path(result["context_dir"]) if result.get("context_dir") else None
        else:
            root = resolve_runtime(args.workspace, args.runtime_root)
            if args.command == "step":
                result = step(root, args.request_id, requests_file=args.requests_file,
                              worklist=args.worklist, updates_file=args.updates_file,
                              response_format=args.response_format)
                code = 0 if result["complete"] else 3
            elif args.command == "check":
                result = check(root, args.request_id)
                code = 0 if result.get("check_passed", result["precheck_passed"]) else 3
            elif args.command == "finish":
                result, code = finish(root, args.request_id)
            elif args.command == "cancel":
                result = cancel(root, args.request_id)
            elif args.command == "view":
                if not args.artifact:
                    raise ValueError("view requires --artifact")
                result = view_page(root, args.request_id, args.artifact, args.pointer,
                                   args.offset, args.page_size, args.string_offset)
            elif args.command == "status" and args.summary:
                result = status_summary(root, args.request_id)
            elif args.command == "status":
                result = status(root, args.request_id)
            _, context = metadata(root, args.request_id) if args.command != "view" else (None, None)
        read_only = args.command == "view" or args.command == "status" and args.summary or args.command == "capabilities"
        if context and context.exists() and not read_only:
            result = compact(result, context)
        output = json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n"
        if not read_only:
            identifier = result.get("request_id") or args.request_id
            runtime_value = result.get("runtime_root") or args.runtime_root or os.environ.get("RELEASE_REVIEW_RUNTIME_DIR")
            if identifier and runtime_value:
                job_file = job_path(Path(runtime_value), identifier)
                if job_file.is_file():
                    job = coordination.read_json(job_file)
                    update(Path(runtime_value), identifier, metrics={**job.get("metrics", {}),
                           "cli_stdout_bytes": job.get("metrics", {}).get("cli_stdout_bytes", 0)
                           + len(output.encode("utf-8")),
                           "agent_tool_round_trips": None, "model_tokens": None})
        print(output, end="")
        return code
    except (ValueError, KeyError, TypeError, OSError, RuntimeError) as exc:
        import sys
        error_output = json.dumps({"error": coordination.safe_error(exc), "request_id": args.request_id,
                                   "terminal": True}, ensure_ascii=False) + "\n"
        if args.command not in ("view", "capabilities") and not (args.command == "status" and args.summary):
            try:
                runtime_value = args.runtime_root or os.environ.get("RELEASE_REVIEW_RUNTIME_DIR")
                if runtime_value and args.request_id and job_path(Path(runtime_value), args.request_id).is_file():
                    job = coordination.read_json(job_path(Path(runtime_value), args.request_id))
                    update(Path(runtime_value), args.request_id, metrics={**job.get("metrics", {}),
                           "cli_stderr_bytes": job.get("metrics", {}).get("cli_stderr_bytes", 0)
                           + len(error_output.encode("utf-8"))})
            except (ValueError, OSError):
                pass
        print(error_output, end="", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
