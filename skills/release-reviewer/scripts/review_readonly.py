"""Read-only draft hints and bounded navigation of saved evidence."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

import review_coordinator as coordination
import review_runtime

LIMIT = 24 * 1024 - 1  # Include the CLI's final newline in the response limit.
MAX_REQUEST_BYTES = 1024 * 1024


def known_citation_bounds(context, manifest, draft):
    """Use checksum-checked read artifacts as hints, never source verification."""
    wanted = set()

    def visit(value):
        if isinstance(value, dict):
            identity = tuple(value.get(key) for key in ("repo", "source", "ref", "path"))
            if all(isinstance(part, str) for part in identity):
                wanted.add(identity)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(draft)
    bounds, artifacts = {}, {}
    for checkpoint in manifest.get("operations", {}).values():
        if not isinstance(checkpoint, dict) or "read_identity" not in checkpoint:
            continue  # Legacy checkpoints have no cheap read index.
        identity = checkpoint["read_identity"]
        if not isinstance(identity, list) or len(identity) != 4 or not all(isinstance(v, str) for v in identity):
            raise ValueError("Owned read checkpoint identity is invalid.")
        identity = tuple(identity)
        if identity not in wanted:
            continue
        artifact = checkpoint.get("artifact")
        if artifact not in artifacts:
            result = review_runtime.load_view(context, artifact)
            if not isinstance(result, dict):
                raise ValueError("Owned read artifact must be an object.")
            saved_identity = tuple(result.get(k) for k in ("repo", "source", "ref", "path"))
            total = result.get("total_lines")
            if total is None and "line_start" not in result and isinstance(result.get("lines"), list):
                total = len(result["lines"])
            if type(total) is not int or total < 0:
                raise ValueError("Owned read artifact has no valid line count.")
            artifacts[artifact] = (saved_identity, total)
            del result
        saved_identity, total = artifacts[artifact]
        if saved_identity != identity:
            raise ValueError("Owned read artifact differs from its checkpoint identity.")
        if identity in bounds and bounds[identity] != total:
            raise ValueError("Saved reads disagree about fixed-file line counts.")
        bounds[identity] = total
    return bounds


def batch_views(context, request_id, requests_file, offset, reader):
    with Path(requests_file).open("rb") as stream:
        raw = stream.read(MAX_REQUEST_BYTES + 1)
    if len(raw) > MAX_REQUEST_BYTES:
        raise ValueError("View requests exceed 1 MiB.")
    requests = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(requests, list) or len(requests) > 100:
        raise ValueError("View requests must be a JSON array of at most 100 operations.")
    if type(offset) is not int or not 0 <= offset <= len(requests):
        raise ValueError("Batch offset exceeds the view requests.")
    identifiers = set()
    for request in requests:
        identifier = request.get("id") if isinstance(request, dict) else None
        if (not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", identifier)
                or identifier in identifiers):
            raise ValueError("Each view request needs a unique id of 1..100 letters, digits, underscores or hyphens.")
        identifiers.add(identifier)
    response = {"schema": "ReleaseReviewViewBatch/v1", "request_id": request_id,
                "requests_sha256": hashlib.sha256(raw).hexdigest(), "offset": offset,
                "total": len(requests), "results": [], "complete": False, "next_index": offset}
    cached_artifact, cached_value = None, None
    for index in range(offset, len(requests)):
        request = requests[index]
        try:
            allowed = {"id", "op", "artifact", "pointer", "offset", "page_size", "string_offset"}
            if set(request) - allowed or request.get("op", "view") != "view":
                raise ValueError("Batch views accept only saved-artifact view operations.")
            artifact = request.get("artifact")
            if not isinstance(artifact, str) or not re.fullmatch(r"[a-f0-9]{64}", artifact):
                raise ValueError("A view artifact must be a 64-character lowercase SHA-256.")
        except ValueError as exc:
            row = {"id": request["id"], "error": str(exc), "code": "view.invalid-request"}
        else:
            # Owned artifact damage propagates; it is not an authoring correction.
            if artifact != cached_artifact:
                cached_value = None
                cached_value = review_runtime.load_view(context, artifact)
                cached_artifact = artifact
            try:
                arguments = {key: value for key, value in request.items() if key not in {"id", "op", "artifact"}}
                result = reader(context, artifact, **arguments, byte_limit=LIMIT - 2048, _value=cached_value)
                row = {"id": request["id"], "result": result}
            except (ValueError, KeyError, IndexError, TypeError) as exc:
                row = {"id": request["id"], "error": coordination.safe_error(exc)[:512], "code": "view.invalid-page"}
        candidate = {**response, "results": [*response["results"], row],
                     "next_index": index + 1 if index + 1 < len(requests) else None,
                     "complete": index + 1 == len(requests)}
        if len(review_runtime.canonical(candidate)) > LIMIT:
            if not response["results"]:
                raise ValueError("A view request cannot fit in the bounded batch response.")
            break
        response = candidate
    if offset == len(requests):
        response.update(complete=True, next_index=None)
    return response
