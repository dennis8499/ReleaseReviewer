"""Read saved evidence in batches and checkpoint only explicitly received pages."""
from __future__ import annotations

from pathlib import Path

import review_coordinator as coordination
import review_runtime


SCHEMA = "ReleaseReviewPaging/v1"
LIMIT = 24 * 1024
PAGE_LIMIT = LIMIT - 2048


class PagingIntegrityError(RuntimeError):
    """An owned checkpoint or evidence artifact failed validation."""


def _artifact(context, identifier):
    try:
        return review_runtime.load_view(context, identifier)
    except (OSError, ValueError) as exc:
        raise PagingIntegrityError(str(exc)) from exc


def value_at(value, pointer):
    if not isinstance(pointer, str) or pointer and not pointer.startswith("/"):
        raise ValueError("view pointer must be a JSON pointer")
    for token in pointer.split("/")[1:]:
        token = token.replace("~1", "/").replace("~0", "~")
        value = value[int(token)] if isinstance(value, list) else value[token]
    return value


def child_pointer(parent, child):
    return parent + "/" + str(child).replace("~", "~0").replace("/", "~1")


def request(artifact, pointer, offset=0, page_size=100, string_offset=0, operation_id=None):
    args = {"artifact": artifact, "pointer": pointer, "offset": offset,
            "page_size": page_size, "string_offset": string_offset}
    return {"id": coordination.digest(args), **args,
            **({"operation_id": operation_id} if operation_id is not None else {})}


def load(context, identifier, artifact):
    if artifact is None:
        return {"schema": SCHEMA, "request_id": identifier, "pending": [], "acknowledged": []}
    state = _artifact(context, artifact)
    if (not isinstance(state, dict) or state.get("schema") != SCHEMA
            or state.get("request_id") != identifier
            or not isinstance(state.get("pending"), list) or not isinstance(state.get("acknowledged"), list)):
        raise PagingIntegrityError("Paging checkpoint does not belong to this Release Reviewer request.")
    return state


def description(context, artifact, state):
    return {"paging_requests_file": str(context / "views" / (artifact + ".json")),
            "paging_artifact": artifact, "pending_count": len(state["pending"]),
            "acknowledged_count": len(state["acknowledged"])}


def enqueue(state, pages):
    known = set(state["acknowledged"]) | {row["id"] for row in state["pending"]}
    for page in pages:
        if page["id"] not in known:
            state["pending"].append(page)
            known.add(page["id"])


def seed(context, state, outcome):
    pages = []
    for row in outcome["operations"]:
        artifact = row["artifact"]
        if isinstance(row["result"], dict) and "content_id" in row["result"]:
            artifact = review_runtime.store_view(context, row["result"])
        pages.append(request(artifact, "", operation_id=row["id"]))
    for identifier, content in outcome.get("contents", {}).items():
        artifact = review_runtime.store_view(context, {"content_id": identifier, **content})
        pages.append(request(artifact, ""))
    enqueue(state, pages)


def _read(context, reader, page, cache):
    artifact = page["artifact"]
    if artifact not in cache:
        cache[artifact] = _artifact(context, artifact)
    value = cache[artifact]
    options = {k: v for k, v in page.items() if k not in {"id", "operation_id"}}
    result = reader(context, **options, byte_limit=PAGE_LIMIT, _value=value)
    return result, value_at(value, page["pointer"])


def _next(page, result, value):
    artifact, pointer = page["artifact"], page["pointer"]
    links = []
    fields, parent = [], pointer
    if isinstance(value, dict) and "value" not in result:
        fields = result["fields"]
    elif isinstance(value, list) and result.get("items"):
        start = result["offset"]
        shown = result["items"]
        actual = value[start:start + len(shown)]
        if shown != actual and isinstance(actual[0], dict):
            fields, parent = shown[0]["fields"], child_pointer(pointer, start)
            if shown[0].get("fields_continue"):
                links.append(shown[0]["fields_continue"])
            if result.get("next_page"):
                links.append(result["next_page"])
    if parent == pointer and result.get("continue"):
        links.append(result["continue"])
    if parent == pointer and result.get("next_page"):
        links.append(result["next_page"])
    for field in fields:
        # Captured patches expose the same text in labelled file/hunk segments.
        owner = value if parent == pointer else value[result["offset"]]
        if (field == "text" and "raw_sha256" in owner and isinstance(owner.get("segments"), list)
                and all(isinstance(x, dict) and isinstance(x.get("text"), str) for x in owner["segments"])
                and "".join(x["text"] for x in owner["segments"]) == owner["text"]):
            continue
        links.append({"artifact": artifact, "pointer": child_pointer(parent, field)})
    return [request(operation_id=page.get("operation_id"),
                    **{key: value for key, value in link.items() if key != "op"}) for link in links]


def view(context, identifier, state, reader, offset=0):
    pending = state["pending"]
    if not 0 <= offset <= len(pending):
        raise ValueError("Paging offset exceeds the current checkpoint.")
    response = {"schema": "ReleaseReviewPagingBatch/v1", "request_id": identifier, "results": [],
                "paging_acknowledgements": [], "pending_count": len(pending),
                "next_index": offset if offset < len(pending) else None}
    cache = {}
    for index in range(offset, min(offset + 100, len(pending))):
        page = pending[index]
        try:
            result, _ = _read(context, reader, page, cache)
        except (IndexError, KeyError, TypeError, ValueError) as exc:
            response["error"] = coordination.safe_error(exc)[:512]
            break
        row = {"id": page["id"], "result": result}
        if "operation_id" in page:
            row["operation_id"] = page["operation_id"]
        candidate = {**response, "results": [*response["results"], row],
                     "paging_acknowledgements": [*response["paging_acknowledgements"], page["id"]],
                     "next_index": index + 1 if index + 1 < len(pending) else None}
        if len(review_runtime.canonical(candidate)) + 2 > LIMIT:
            if not response["results"]:
                response["error"] = "Evidence page and controls exceed the response limit."
            break
        response = candidate
    return response


def acknowledge(context, state, identifiers, reader):
    if (not isinstance(identifiers, list) or len(identifiers) > 100
            or not all(isinstance(key, str) for key in identifiers)
            or len(identifiers) != len(set(identifiers))):
        raise ValueError("/paging_acknowledgements must list up to 100 unique received page IDs.")
    pending = {row["id"]: row for row in state["pending"]}
    if any(key not in pending for key in identifiers):
        raise ValueError("/paging_acknowledgements contains a page outside the current checkpoint.")
    cache, following = {}, []
    for key in identifiers:
        page = pending[key]
        result, value = _read(context, reader, page, cache)
        following.extend(_next(page, result, value))
    acknowledged = set(identifiers)
    state["acknowledged"].extend(identifiers)
    state["pending"] = [page for page in state["pending"] if page["id"] not in acknowledged]
    enqueue(state, following)
    return review_runtime.store_view(context, state)


def from_file(context, identifier, path):
    file = Path(path).absolute()
    # The managed paging state is immutable and verified by its owned artifact ID.
    if (file.parent == context.resolve() / "views" and file.suffix == ".json"
            and coordination.DIGEST.fullmatch(file.stem)):
        state = _artifact(context, file.stem)
        if isinstance(state, dict) and state.get("schema") == SCHEMA:
            if state.get("request_id") != identifier:
                raise ValueError("Paging checkpoint belongs to a different request.")
            return state
    return None
