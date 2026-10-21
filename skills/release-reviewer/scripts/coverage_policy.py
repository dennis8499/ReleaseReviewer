"""Coverage policy for already-contained heads, independently of evidence transport."""
from __future__ import annotations

import math
import hashlib
from pathlib import Path
import re

import frozen_evidence
import git_support as git
import review_session

COVERAGE_POLICY = "ancestor-noop/v1"
OID = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def _sources(record):
    snapshot = record.get("snapshot")
    require(isinstance(snapshot, dict) and snapshot.get("policy") == frozen_evidence.POLICY,
            "coverage policy needs an owned snapshot")
    sources = snapshot.get("sources")
    require(isinstance(sources, dict) and sources, "coverage policy needs captured sources")
    return sources


def _source(sources, reference):
    require(isinstance(reference, str) and OID.fullmatch(reference), "coverage policy ref is invalid")
    source = sources.get(reference)
    require(isinstance(source, dict) and isinstance(source.get("tree"), str)
            and OID.fullmatch(source["tree"]) and isinstance(source.get("entries"), list),
            "coverage policy source identity is missing")
    return source


def ancestor_noop(manifest: dict) -> bool:
    """Derive eligibility; a policy marker alone never grants an exemption."""
    if "coverage_policy" not in manifest:
        return False
    require(manifest["coverage_policy"] == COVERAGE_POLICY, "unsupported coverage policy")
    require(manifest.get("snapshot_policy") == frozen_evidence.POLICY and manifest.get("mode") == "merge",
            "coverage policy needs a frozen merge context")
    catalog = manifest.get("repositories")
    require(isinstance(catalog, list) and catalog and all(isinstance(record, dict) for record in catalog),
            "coverage policy Repo catalog is invalid")
    entries = [record for record in catalog if record.get("id") == "entry"]
    require(len(entries) == 1 and entries[0].get("role") == "entry" and entries[0].get("status") == "captured",
            "coverage policy needs one captured entry Repo")
    sources = _sources(entries[0])
    for field in ("base_sha", "head_sha", "merge_base", "diff_base"):
        _source(sources, manifest.get(field))
    require(manifest["merge_base"] == manifest["diff_base"], "coverage policy comparison baseline differs")
    for field in ("changed_files", "release_changes", "merge_commits", "context_gaps"):
        require(isinstance(manifest.get(field), list), "coverage policy metadata is invalid: " + field)
    require(type(manifest.get("context_complete")) is bool
            and manifest["context_complete"] == (not manifest["context_gaps"]),
            "coverage policy context completeness differs")
    require(type(manifest.get("working_tree_unchanged")) is bool, "coverage policy worktree state is invalid")
    preview = manifest.get("merge_preview")
    require(isinstance(preview, dict) and preview.get("status") in {"clean", "conflicts", "unavailable"}
            and isinstance(preview.get("changed_files"), list), "coverage policy merge preview is invalid")
    status, tree = preview["status"], preview.get("tree_sha")
    if status in {"clean", "conflicts"}:
        require(type(preview.get("exit_code")) is int and preview["exit_code"] == (0 if status == "clean" else 1),
                "coverage policy preview outcome differs")
        require(_source(sources, tree)["tree"] == tree, "coverage policy preview tree identity differs")
    else:
        require(tree is None, "unavailable preview exposes a tree")
    if manifest["head_sha"] != manifest["merge_base"]:
        return False
    require(not manifest["changed_files"], "ancestor head has inconsistent committed changes")
    require(not manifest["merge_commits"], "ancestor head has inconsistent merge commits")
    if status != "clean":
        return False
    require(tree == sources[manifest["base_sha"]]["tree"], "ancestor preview differs from the base tree")
    require(not manifest["release_changes"], "ancestor preview has inconsistent release changes")
    if not manifest["working_tree_unchanged"] or not manifest["context_complete"]:
        return False
    if any(record.get("status") != "captured" for record in catalog):
        return False
    for record in catalog:
        captured = _sources(record)
        if record.get("id") != "entry":
            require(record.get("role") == "supporting", "coverage policy Repo role is invalid")
            _source(captured, record.get("ref"))
    path = manifest.get("ancestor_path")
    require(isinstance(path, list) and path and all(isinstance(reference, str) and OID.fullmatch(reference)
            for reference in path) and len(path) == len(set(path))
            and path[0] == manifest["base_sha"] and path[-1] == manifest["head_sha"],
            "ancestor coverage needs a fixed base-to-head commit path")
    return True


def validate_commit_path(raw: bytes, path: list[str], object_format: str):
    """Verify one raw batch of commit bodies, their hashes and ordered parent edges."""
    require(isinstance(object_format, str) and object_format in {"sha1", "sha256"},
            "ancestor proof object format is invalid")
    require(isinstance(raw, bytes) and isinstance(path, list) and path
            and all(isinstance(reference, str) and OID.fullmatch(reference) for reference in path)
            and len(path) == len(set(path)), "ancestor proof batch identity is invalid")
    offset = 0
    for index, reference in enumerate(path):
        boundary = raw.find(b"\n", offset)
        require(boundary >= 0, "ancestor proof commit header is incomplete")
        header = raw[offset:boundary].split()
        require(len(header) == 3 and header[0] == reference.encode("ascii")
                and header[1] == b"commit" and header[2].isdigit(), "ancestor proof commit identity differs")
        size = int(header[2])
        offset = boundary + 1
        body = raw[offset:offset + size]
        offset += size
        require(len(body) == size and raw[offset:offset + 1] == b"\n", "ancestor proof commit body is incomplete")
        offset += 1
        digest = hashlib.new(object_format, b"commit " + str(size).encode("ascii") + b"\0" + body).hexdigest()
        require(digest == reference, "ancestor proof commit checksum differs")
        if index + 1 < len(path):
            parents = [line[7:].decode("ascii") for line in body.partition(b"\n\n")[0].split(b"\n")
                       if line.startswith(b"parent ")]
            require(path[index + 1] in parents, "ancestor proof parent link differs")
    require(offset == len(raw), "ancestor proof has unexpected trailing bytes")


def _validate_ancestor_path(manifest, context, record):
    path = manifest["ancestor_path"]
    raw = git.run_git(frozen_evidence.repository(context, record), ["cat-file", "--batch"], binary=True,
                      input_bytes=("\n".join(path) + "\n").encode("ascii"),
                      env=frozen_evidence.environment(context, record))
    validate_commit_path(raw, path, record["snapshot"].get("object_format"))


def validate(manifest: dict, context: Path) -> bool:
    """Recheck required ref/tree identities using owned objects and offline Git."""
    noop = ancestor_noop(manifest)
    if not noop:
        return False
    context = review_session.validate_session(context)
    require(manifest.get("diff_roots") == [], "ancestor coverage has inconsistent Diff roots")
    timeout = manifest.get("git_timeout", 180.0)
    require(type(timeout) in (int, float) and math.isfinite(timeout) and timeout > 0,
            "coverage policy Git timeout is invalid")
    token = git.GIT_TIMEOUT_SECONDS.set(timeout)
    try:
        entry = next(record for record in manifest["repositories"] if record["id"] == "entry")
        _validate_ancestor_path(manifest, context, entry)
        for record in manifest["repositories"]:
            if record.get("status") != "captured":
                continue
            sources = _sources(record)
            references = ({manifest["base_sha"], manifest["head_sha"], manifest["merge_base"]}
                          if record["id"] == "entry" else {record.get("ref")})
            if record["id"] == "entry" and manifest["merge_preview"].get("tree_sha"):
                references.add(manifest["merge_preview"]["tree_sha"])
            repo = frozen_evidence.repository(Path(context), record)
            env = frozen_evidence.environment(Path(context), record)
            for reference in sorted(references):
                source = _source(sources, reference)
                actual = str(git.run_git(repo, ["rev-parse", f"{reference}^{{tree}}"], env=env)).strip()
                require(actual == source["tree"], "coverage policy owned tree identity differs")
        return noop
    except git.ReviewContextError as exc:
        raise ValueError(f"coverage policy owned evidence is invalid: {exc}") from exc
    finally:
        git.GIT_TIMEOUT_SECONDS.reset(token)
