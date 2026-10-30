"""Owned-context generation checks and optional compact evidence transport."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
import os
from pathlib import Path
import uuid

from git_network_lease import DirectoryLease, extended_windows_path

_WRITERS = ContextVar("review_context_writers", default=frozenset())


@contextmanager
def writer(context, timeout=180.0):
    context = Path(context).resolve()
    key = str(context)
    if key in _WRITERS.get():
        yield
        return
    marker = context / ".release-reviewer-owned.json"
    ownership = json.loads(marker.read_text(encoding="utf-8")) if marker.is_file() else {}
    if isinstance(ownership, dict) and ownership.get("schema") == "release-reviewer-session/v2":
        import review_coordinator
        import review_session
        review_session.validate_session(context)
        lease = review_coordinator.FileLock(Path(ownership["runtime"]) / "jobs" /
            (ownership["request_id"] + ".manifest.lock"), timeout=timeout).acquire()
    else:
        lease = DirectoryLease(context, name="manifest-writer.lock", kind="manifest", timeout=timeout).acquire()
    token = _WRITERS.set(_WRITERS.get() | {key})
    try:
        yield
    finally:
        _WRITERS.reset(token)
        lease.release()


def save_manifest(context, manifest):
    context = Path(context).resolve()
    with writer(context):
        target = context / "manifest.json"
        if target.is_symlink():
            raise ValueError("Manifest cannot be a link")
        expected = manifest.get("generation", 0)
        if type(expected) is not int or expected < 0:
            raise ValueError("Invalid manifest generation")
        actual = json.loads(target.read_text(encoding="utf-8-sig")).get("generation", 0) if target.exists() else 0
        if actual != expected:
            raise ValueError("Manifest generation changed; reload before writing")
        updated = {**manifest, "generation": expected + 1}
        temporary = context / (".manifest-" + uuid.uuid4().hex + ".json")
        try:
            temporary.write_text(json.dumps(updated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        manifest["generation"] = expected + 1


def canonical(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def batch_blobs(run, items, object_format):
    """One raw cat-file process; validate every expression, OID and byte body."""
    if object_format not in ("sha1", "sha256"):
        raise ValueError("Unsupported object format")
    unique = {}
    for expression, expected in items:
        if expression in unique and unique[expression] != expected:
            raise ValueError("Conflicting fixed batch blob identity")
        unique[expression] = expected
    if any("\n" in expression or "\r" in expression for expression in unique):
        raise ValueError("Line-delimited batch cannot represent this Git path")
    raw = run(("\n".join(unique) + "\n").encode("utf-8"))
    offset, result = 0, {}
    for expression, expected in unique.items():
        boundary = raw.find(b"\n", offset)
        if boundary < 0:
            raise ValueError("Incomplete cat-file batch header")
        header = raw[offset:boundary].split()
        if len(header) != 3 or header[1] != b"blob" or header[0].decode("ascii") != expected:
            raise ValueError("Fixed batch blob identity differs")
        size = int(header[2])
        offset = boundary + 1
        content = raw[offset:offset + size]
        offset += size
        if len(content) != size or raw[offset:offset + 1] != b"\n":
            raise ValueError("Incomplete cat-file batch body")
        offset += 1
        digest = hashlib.new(object_format, b"blob " + str(size).encode("ascii") + b"\0" + content).hexdigest()
        if digest != expected:
            raise ValueError("Fixed batch blob checksum differs")
        result[expression] = content
    if offset != len(raw):
        raise ValueError("Unexpected cat-file batch trailing data")
    return result


def shared_reads(value):
    contents, reads = {}, []
    for item in value["reads"]:
        identifier = item["sha256"]
        contents.setdefault(identifier, {"lines": item["lines"]})
        reads.append({**{key: content for key, content in item.items() if key != "lines"}, "content_id": identifier})
    return {"schema": "MergeReviewEvidenceBatch/v1", "reads": reads, "contents": contents}


def _extended_windows_path(path):
    """Keep artifact views on the same shared Windows path policy as runtime files."""
    return extended_windows_path(path)


def path_exists(path):
    return os.path.exists(_extended_windows_path(path))


def path_is_symlink(path):
    return os.path.islink(_extended_windows_path(path))


def path_mkdir(path, *, exist_ok=False):
    target = _extended_windows_path(path)
    try:
        os.mkdir(target)
    except FileExistsError:
        if not exist_ok or not os.path.isdir(target):
            raise


def path_read_text(path, *, encoding="utf-8"):
    with open(_extended_windows_path(path), "r", encoding=encoding) as stream:
        return stream.read()


def path_write_text(path, value, *, encoding="utf-8", exclusive=False):
    with open(_extended_windows_path(path), "x" if exclusive else "w", encoding=encoding) as stream:
        return stream.write(value)


def path_children(path, *, suffix=None):
    with os.scandir(_extended_windows_path(path)) as entries:
        return [Path(path) / entry.name for entry in entries
                if suffix is None or entry.name.endswith(suffix)]


def _view_exists(path):
    return path_exists(path)


def _view_is_symlink(path):
    return path_is_symlink(path)


def _view_read(path):
    with open(_extended_windows_path(path), "rb") as stream:
        return stream.read()


def _view_write_exclusive(path, value):
    with open(_extended_windows_path(path), "xb") as stream:
        stream.write(value)


def _view_unlink(path):
    try:
        os.unlink(_extended_windows_path(path))
    except FileNotFoundError:
        pass


def _views(context):
    root = Path(context).resolve()
    directory = root / "views"
    if path_is_symlink(directory) or (path_exists(directory) and directory.resolve() != directory):
        raise ValueError("Evidence view directory cannot be a link")
    path_mkdir(directory, exist_ok=True)
    return directory


def store_view(context, value):
    raw = canonical(value)
    identifier = hashlib.sha256(raw).hexdigest()
    target = _views(context) / (identifier + ".json")
    if _view_is_symlink(target):
        raise ValueError("Evidence view cannot be a link")
    temporary = target.with_name(".view-" + uuid.uuid4().hex)
    try:
        _view_write_exclusive(temporary, raw)
        try:
            os.link(_extended_windows_path(temporary), _extended_windows_path(target))
        except FileExistsError:
            if _view_read(target) != raw:
                raise ValueError("Evidence view identity mismatch")
        except OSError:
            # Some managed Windows temp volumes deny hard links. A same-volume
            # rename is still atomic and, on Windows, fails if another writer
            # already installed the content-addressed target.
            if os.name != "nt" or _view_exists(target):
                if not _view_exists(target) or _view_is_symlink(target) or _view_read(target) != raw:
                    raise ValueError("Evidence view could not be installed safely")
            else:
                try:
                    os.rename(_extended_windows_path(temporary), _extended_windows_path(target))
                except FileExistsError:
                    if _view_is_symlink(target) or _view_read(target) != raw:
                        raise ValueError("Evidence view identity mismatch")
            if _view_is_symlink(target) or _view_read(target) != raw:
                raise ValueError("Evidence view identity mismatch")
    finally:
        _view_unlink(temporary)
    return identifier


def load_view(context, identifier):
    if not isinstance(identifier, str) or len(identifier) != 64 or any(c not in "0123456789abcdef" for c in identifier):
        raise ValueError("Invalid evidence artifact ID")
    path = _views(context) / (identifier + ".json")
    if _view_is_symlink(path):
        raise ValueError("Evidence view cannot be a link")
    raw = _view_read(path)
    if hashlib.sha256(raw).hexdigest() != identifier:
        raise ValueError("Evidence view checksum mismatch")
    return json.loads(raw)


def select_searches(value, identifiers):
    """Select saved searches, retaining batch failures and original item indexes."""
    if not identifiers or any(not isinstance(item, str) or len(item) != 64 or
            any(char not in "0123456789abcdef" for char in item) for item in identifiers):
        raise ValueError("--search-id must be a 64-hex search hash")
    if not isinstance(value, dict):
        raise ValueError("--search-id requires a search artifact")
    selected = set(identifiers)
    if "results" not in value:
        available = {value["search_id"]} if isinstance(value.get("search_id"), str) and isinstance(value.get("matches"), list) else set()
        if not available:
            raise ValueError("--search-id requires a search artifact")
        unknown = selected - available
        if unknown:
            raise ValueError("Unknown search ID in artifact: " + ", ".join(sorted(unknown)))
        return value
    rows = value["results"]
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("Search artifact results must be an array of objects")

    def search_id(row):
        result = row.get("result")
        if row.get("ok") is True and isinstance(result, dict) and isinstance(result.get("matches"), list):
            identifier = result.get("search_id")
            return identifier if isinstance(identifier, str) else None
        return None

    available = {search_id(row) for row in rows if isinstance(search_id(row), str)}
    if not available:
        raise ValueError("--search-id requires an artifact containing successful searches")
    unknown = selected - available
    if unknown:
        raise ValueError("Unknown search ID in artifact: " + ", ".join(sorted(unknown)))
    result = {**value, "results": [row for row in rows if row.get("ok") is False or search_id(row) in selected]}
    if "contents" in result:
        referenced = {row["result"]["content_id"] for row in result["results"]
                      if row.get("ok") is True and "content_id" in row.get("result", {})}
        result["contents"] = {key: content for key, content in value["contents"].items() if key in referenced}
    return result


def page(value, context, *, size, offset=0, artifact=None):
    if type(size) is not int or size < 1 or type(offset) is not int or offset < 0:
        raise ValueError("Page size must be positive and offset nonnegative")
    identifier = artifact or store_view(context, value)
    def visit(item):
        if isinstance(item, list):
            return [visit(child) for child in item]
        if not isinstance(item, dict):
            return item
        result = {key: visit(child) for key, child in item.items() if key != "output" or "matches" not in item}
        if isinstance(item.get("matches"), list):
            total = len(item["matches"])
            result["matches"] = item["matches"][offset:offset + size]
            result["view"] = {"total_matches": total, "offset": offset,
                              "next_offset": offset + size if offset + size < total else None,
                              "view_complete": offset == 0 and size >= total,
                              "evidence_complete": True}
        return result
    return {"schema": "ReviewEvidenceView/v1", "artifact": identifier,
            "artifact_path": str(_views(context) / (identifier + ".json")), "result": visit(value)}
