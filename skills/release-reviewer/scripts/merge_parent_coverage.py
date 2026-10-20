"""Completion records only for merge-parent views relevant to final changes."""
POLICY = "necessary-merge-parents/v1"


def required(changes, merges, final_paths=None):
    active = {path for change in changes for path in (change.get("path"), change.get("old_path")) if path}
    result = []
    for merge in merges:
        for view in merge["parent_views"]:
            relevant = [change for change in view["changes"] if active & {change.get("path"), change.get("old_path")}]
            if relevant:
                paths = sorted({path for change in relevant for path in (change.get("path"), change.get("old_path")) if path in active})
                result.append({"id": f"M-{merge['sha'][:12]}-P{view['position']}",
                               "commit": merge["sha"], "parent": view["sha"],
                               "paths": paths,
                               "final_content_available": final_paths is None or bool(set(paths) & set(final_paths)),
                               "parent_content_available": any(change.get("old_mode") not in (None, "000000", "160000")
                                                               and not change.get("binary") for change in relevant)})
    return result


def unfinished(views):
    return [{"id": view["id"], "status": "not-reviewed", "reason": "尚未對照必要 parent 與最終版本。", "evidence": []} for view in views]


def validate(views, rows, citations, *, final_ref, parent_repo, require):
    require(isinstance(rows, list), "merge_parent_review must be an array")
    expected = {view["id"]: view for view in views}
    seen, complete = set(), True
    for row in rows:
        require(isinstance(row, dict) and row.get("id") in expected and row["id"] not in seen,
                "merge_parent_review has extra/duplicate IDs")
        seen.add(row["id"])
        view = expected[row["id"]]
        require(row.get("status") in ("reviewed", "not-reviewed"), "invalid merge-parent review status")
        require(isinstance(row.get("reason"), str) and row["reason"].strip(), "merge-parent review needs a final-version judgment")
        evidence = row.get("evidence", [])
        require(isinstance(evidence, list), "merge-parent evidence must be an array")
        citations(evidence)
        if row["status"] == "reviewed":
            if view.get("final_content_available", True):
                require(any(item.get("repo") == parent_repo and item.get("ref") == final_ref and item.get("path") in view["paths"] for item in evidence),
                        "merge-parent review needs final-version evidence")
            else:
                require(row.get("final_ref") == final_ref, "merge-parent absence judgment needs its fixed final_ref")
            if view["parent_content_available"]:
                require(any(item.get("repo") == parent_repo and item.get("ref") == view["parent"] and item.get("path") in view["paths"] for item in evidence),
                        "merge-parent review needs its fixed parent evidence")
        else:
            complete = False
    require(seen == set(expected), "merge_parent_review omits necessary views")
    return complete
