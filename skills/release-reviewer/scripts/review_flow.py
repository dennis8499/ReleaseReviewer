"""Persistent, independently owned Release Reviewer request facade."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import time
import uuid

import review_coordinator as coordination
import review_runtime
import review_session

SCHEMA = "ReleaseReviewSession/v1"
LIMIT = 24 * 1024
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
            if not final_evidence(evidence):
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


def start(args):
    import git_review_context as capture
    import git_support as git
    import group_context
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
        try:
            repositories, _ = group_context.discover(Path(args.workspace).resolve(), Path(args.workspace_file) if args.workspace_file else None)
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
            with active.activate():
                active.check()
                manifest = capture.build_manifest(capture_args)
                active.check()
            update(root, identifier, state="ready", phase="review", selected_repo="entry",
                   network_activity=manifest["network_activity"],
                   fixed_identity=fixed_identity(manifest),
                   metrics={"capture_seconds": time.perf_counter() - began})
            summary = capture.capture_summary_v2(manifest)
            return {"schema": "ReleaseReviewStart/v1", "tool": "release-reviewer", "request_id": identifier, "runtime_root": str(root),
                    "state": "ready", "context_dir": str(context), "draft_path": str(context / "review-result-draft.json"),
                    **fixed_identity(manifest),
                    "selected_repo": "entry", "entry_repo": "entry", "repositories": repo_index(manifest),
                    "required_coverage": coordination.read_json(context / "review-worklist.json")["required_coverage"],
                    "required_merge_views": manifest.get("required_merge_views", []),
                    "workspace_instructions": manifest.get("workspace_instructions", []),
                    "network_activity": manifest["network_activity"],
                    "files": summary["files"], "step": {"operations": ["read", "search", "list", "instructions", "patch", "view"],
                    "requests_file": "UTF-8 JSON array, each operation needs unique id and op"}}
        except BaseException as exc:
            update(root, identifier, state="cancelled" if active.cancelled() else "failed", phase="terminal",
                   error=coordination.safe_error(exc), finished_at_epoch=time.time())
            if context and context.exists():
                review_session.cleanup_session(context)
            raise
    finally:
        owner.release()


def artifact_view(context, artifact, pointer="", offset=0, page_size=100, string_offset=0):
    value = review_runtime.load_view(context, artifact)
    if not isinstance(pointer, str) or pointer and not pointer.startswith("/"):
        raise ValueError("view pointer must be a JSON pointer")
    for token in pointer.split("/")[1:]:
        token = token.replace("~1", "/").replace("~0", "~")
        value = value[int(token)] if isinstance(value, list) else value[token]
    if type(offset) is not int or offset < 0 or type(page_size) is not int or not 1 <= page_size <= 1000:
        raise ValueError("invalid view page")
    if isinstance(value, list):
        items = value[offset:offset + page_size]
        while len(review_runtime.canonical(items)) > LIMIT // 2 and len(items) > 1:
            items = items[:max(1, len(items) // 2)]
        end = offset + len(items)
        return {"artifact": artifact, "pointer": pointer, "items": items, "total": len(value),
                "next_offset": end if end < len(value) else None, "view_complete": end >= len(value)}
    if isinstance(value, str):
        if type(string_offset) is not int or string_offset < 0:
            raise ValueError("invalid string offset")
        text = value[string_offset:string_offset + 2048]
        end = string_offset + len(text)
        return {"artifact": artifact, "pointer": pointer, "text": text,
                "next_string_offset": end if end < len(value) else None, "view_complete": end >= len(value)}
    return {"artifact": artifact, "pointer": pointer, "value": value,
            "fields": list(value) if isinstance(value, dict) else None}


def compact(value, context):
    if len(review_runtime.canonical(value)) <= LIMIT:
        return {**value, "outcome_available": True}
    artifact = review_runtime.store_view(context, value)
    return {"schema": "ReleaseReviewEvidenceSummary/v1", "artifact": artifact,
            "evidence_complete": True, "view_complete": False,
            **{key: value[key] for key in ("request_id", "runtime_root", "context_dir", "draft_path", "state", "complete", "next_index", "error") if key in value},
            "fields": list(value), "continue": {"op": "view", "artifact": artifact, "pointer": "", "offset": 0, "page_size": 100}}


def step(root, identifier, operations=None, *, requests_file=None):
    import evidence as ev
    import frozen_evidence
    path = job_path(root, identifier)
    with coordination.FileLock(path.with_suffix(".owner.lock")):
        job, context = metadata(root, identifier)
        if job["state"] not in ("ready", "reviewing"):
            raise ValueError("Session is not ready for review.")
        active = coordination.Coordination(root, identifier)
        results, checked, changed = [], set(), False
        began = time.perf_counter()
        try:
            with active.activate(), review_runtime.writer(context):
                manifest = ev.load_manifest(context, full=False)
                checkpoints = manifest.setdefault("operations", {})
                ev.require(isinstance(checkpoints, dict) and all(
                    isinstance(key, str) and isinstance(value, dict)
                    and isinstance(value.get("fingerprint"), str) and coordination.DIGEST.fullmatch(value["fingerprint"])
                    and isinstance(value.get("artifact"), str) and coordination.DIGEST.fullmatch(value["artifact"])
                    for key, value in checkpoints.items()), "Owned operation checkpoints are invalid.")
                def input_failure(message, code="authoring.invalid-requests-file", pointer="/requests_file"):
                    errors = [{"code": code, "pointer": pointer, "message": message}]
                    update(root, identifier, state="reviewing", phase="input-repair", errors=errors)
                    return {"request_id": identifier, "state": "reviewing", "phase": "input-repair",
                            "operations": [], "complete": False, "next_index": 0, "error": message, "errors": errors,
                            "terminal": False, "recoverable": True, "context_retained": True,
                            "evidence_verified": False, "git_commands": 0}
                if requests_file is not None:
                    try:
                        operations = coordination.read_json(requests_file)
                    except (OSError, ValueError) as exc:
                        return input_failure(coordination.safe_error(exc))
                if not isinstance(operations, list) or not operations:
                    return input_failure("operations must be a nonempty array", "authoring.invalid-operations", "")
                with ev.operation_cache(manifest, context, persistent=True):
                    for index, operation in enumerate(operations):
                        active.check()
                        try:
                            if not isinstance(operation, dict):
                                raise ValueError("operation must be an object")
                            op_id = operation.get("id")
                            import re
                            if not isinstance(op_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", op_id):
                                raise ValueError("operation requires a unique id")
                            fingerprint = coordination.digest(operation)
                            old = manifest["operations"].get(op_id)
                            if old:
                                if old["fingerprint"] != fingerprint:
                                    raise ValueError("operation id already has different input")
                            kind = operation.get("op")
                            args = {key: value for key, value in operation.items() if key not in ("id", "op")}
                            if kind not in ("read", "search", "list", "instructions", "patch", "view"):
                                raise ValueError("unsupported step operation")
                            repo_id = args.get("repo", "entry")
                            repo = ev.repository(manifest, repo_id)
                            if kind != "view" and repo.get("status") != "captured":
                                raise ValueError(repo.get("reason") or "Requested Repo was not captured.")
                        except (ValueError, KeyError, TypeError, OSError) as exc:
                            outcome = {"operations": results, "complete": False, "next_index": index, "error": str(exc)}
                            break
                        if old:
                            # A saved successful checkpoint is owned evidence,
                            # so damage is fatal rather than an authoring typo.
                            result = review_runtime.load_view(context, old["artifact"])
                            results.append({"id": op_id, "reused": True, "artifact": old["artifact"], "result": result})
                            continue
                        # Source damage is terminal. Validation is once per used
                        # Repo in this round, never for pure artifact navigation.
                        if kind != "view" and repo_id not in checked:
                            frozen_evidence.validate(context, repo)
                            checked.add(repo_id)
                        try:
                            if kind in ("read", "search", "list"):
                                if args.get("source") is None:
                                    args["source"] = "head" if repo_id == "entry" else "supporting"
                                result = ev.request_result(manifest, context, {"action": kind, **args})
                            elif kind == "view":
                                result = artifact_view(context, **args)
                            elif kind == "patch":
                                if set(args) - {"path", "repo"} or args.get("path") not in ("diff.patch", "release.patch", "merge-output.txt"):
                                    raise ValueError("patch must name a captured patch")
                                result = {"path": args["path"], "text": (context / args["path"]).read_text(encoding="utf-8")}
                            else:
                                if set(args) - {"repo"}:
                                    raise ValueError("unknown instructions argument")
                                source = "head" if repo_id == "entry" else "supporting"
                                ref, _ = ev.source_version(manifest, context, source, repo=repo_id)
                                result = {"instructions": [{**item, "content": ev.read_blob(manifest, context, source, item["path"], repo=repo_id).decode("utf-8")}
                                                          for item in repo.get("instructions", []) if item["ref"] == ref]}
                            artifact = review_runtime.store_view(context, result)
                            manifest["operations"][op_id] = {"fingerprint": fingerprint, "artifact": artifact}
                            changed = True
                            results.append({"id": op_id, "artifact": artifact, "result": result})
                        except ev.EVIDENCE_ERRORS as exc:
                            outcome = {"operations": results, "complete": False, "next_index": index, "error": str(exc)}
                            break
                    else:
                        outcome = {"operations": results, "complete": True, "next_index": len(operations)}
                    cache = ev._cache(manifest, context)
                    outcome["search_activity"] = {"executed": cache["search_runs"], "cache_hits": cache["search_hits"]}
                if changed:
                    review_runtime.save_manifest(context, manifest)
            update(root, identifier, state="reviewing", phase="review", last_step={key: outcome[key] for key in ("complete", "next_index")},
                   errors=[],
                   metrics={**job.get("metrics", {}), "read_search_seconds": job.get("metrics", {}).get("read_search_seconds", 0) + time.perf_counter() - began})
            return {"request_id": identifier, "state": "reviewing", "phase": "review", "terminal": False,
                    "recoverable": not outcome["complete"], "context_retained": True, **outcome}
        except BaseException as exc:
            update(root, identifier, state="cancelled" if active.cancelled() else "failed", phase="terminal",
                   error=coordination.safe_error(exc), finished_at_epoch=time.time())
            review_session.cleanup_session(context)
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
    try:
        if context.exists():
            review_session.cleanup_session(context)
    except (OSError, ValueError) as exc:
        update(root, identifier, cleanup_pending=True, cleanup_error=coordination.safe_error(exc))
    else:
        update(root, identifier, cleanup_pending=False, cleanup_error=None)
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
        def reviewed(outcome):
            nonlocal validated_outcome
            validated_outcome = outcome
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
            if Path(frozen["repo"]) != Path(job["inputs"]["project"]):
                raise ValueError("Captured entry Repo differs from this request's reviewed Repo.")
            update(root, identifier, state="validating", phase="validation")
            with active.activate():
                report = release_report.publish_report(context, context / "review-result-draft.json",
                    retain_on_validation_error=True, on_stage=staged, on_published=published,
                    on_review_outcome=reviewed, cleanup_context=False)
            update(root, identifier, metrics={**job.get("metrics", {}), "validation_publish_seconds": time.perf_counter() - began})
            return clean_published(root, identifier, context)
        except release_report.DraftValidationError as exc:
            update(root, identifier, state="reviewing", phase="draft-repair", errors=[str(exc)])
            result = check(root, identifier)
            if not result["errors"]:
                result["errors"] = [{"code": "full_validation", "pointer": "", "message": str(exc)}]
            return {**result, "state": "reviewing", "phase": "draft-repair", "terminal": False,
                    "recoverable": True, "context_retained": True}, 3
        except BaseException as exc:
            if coordination.read_json(path)["state"] == "published":
                raise
            update(root, identifier, state="cancelled" if active.cancelled() else "failed", phase="terminal", error=coordination.safe_error(exc), finished_at_epoch=time.time())
            if context.exists():
                review_session.cleanup_session(context)
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
                    review_session.cleanup_session(context)
                update(root, identifier, state="cancelled", phase="terminal", finished_at_epoch=time.time())
        finally:
            lock.release()
    return status(root, identifier)


def parser():
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("command", choices=("start", "step", "check", "finish", "status", "cancel"))
    for name in ("workspace", "workspace-file", "runtime-root", "request-id", "project", "base", "head"):
        value.add_argument("--" + name)
    value.add_argument("--requests-file", type=Path)
    value.add_argument("--workspace-context-json", type=json.loads, default={})
    value.add_argument("--refresh", action="store_true")
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
        if args.command == "start":
            if not all((args.workspace, args.project, args.base, args.head)):
                raise ValueError("start requires workspace, project, base and head")
            result = start(args)
            context = Path(result["context_dir"]) if result.get("context_dir") else None
        else:
            root = resolve_runtime(args.workspace, args.runtime_root)
            if args.command == "step":
                result = step(root, args.request_id, requests_file=args.requests_file)
                code = 0 if result["complete"] else 3
            elif args.command == "check":
                result = check(root, args.request_id)
                code = 0 if result.get("check_passed", result["precheck_passed"]) else 3
            elif args.command == "finish":
                result, code = finish(root, args.request_id)
            elif args.command == "cancel":
                result = cancel(root, args.request_id)
            else:
                result = status(root, args.request_id)
            _, context = metadata(root, args.request_id)
        if context and context.exists():
            result = compact(result, context)
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        return code
    except (ValueError, KeyError, TypeError, OSError, RuntimeError) as exc:
        import sys
        print(json.dumps({"error": coordination.safe_error(exc), "request_id": args.request_id, "terminal": True}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
