"""Validate fixed supporting evidence and connected functional-chain coverage."""
from __future__ import annotations

import evidence as ev

KINDS = {"api", "event", "contract", "database", "cache", "other"}


def nonempty(value, label, require=ev.require):
    require(isinstance(value, str) and value.strip(), f"{label} 必須有具體說明。")
    return value.strip()


def validate(draft, manifest, context, citations, *, require=ev.require):
    catalog = {r["id"]: r for r in manifest["repositories"]}
    for field in ("supporting_coverage", "functional_chain", "chain_gaps"):
        require(isinstance(draft.get(field), list), f"{field} 必須是陣列。")
    require(all(isinstance(g, str) and g.strip() for g in draft["chain_gaps"]), "功能鏈缺口須為文字。")
    coverage, searches = {}, {}
    complete = not draft["chain_gaps"]
    for item in draft["supporting_coverage"]:
        require(isinstance(item, dict), "關聯覆蓋項目須為物件。")
        repo = item.get("repo")
        require(isinstance(repo, str) and repo in catalog and repo != "entry" and repo not in coverage,
                "關聯覆蓋 Repo 重複或無效。")
        status = item.get("status")
        require(isinstance(status, str) and status in {"reviewed", "unrelated", "not-reviewed"}, "關聯覆蓋狀態無效。")
        reason = nonempty(item.get("reason"), "關聯判定", require)
        evidence = citations(item.get("evidence", []), manifest, context)
        require(all(e["repo"] == repo and e["source"] == "supporting" for e in evidence), "關聯覆蓋證據 Repo 不符。")
        ids = item.get("searches", [])
        require(isinstance(ids, list), "關聯搜尋必須是陣列。")
        if status != "not-reviewed":
            require(catalog[repo]["status"] == "captured" and bool(ids), "完整關聯判定須有固定全 Repo 搜尋紀錄。")
            require(status != "reviewed" or bool(evidence), "已審查的功能鏈 Repo 須有內容證據。")
        else:
            complete = False
        for identifier in ids:
            require(isinstance(identifier, str) and len(identifier) == 64
                    and all(c in "0123456789abcdef" for c in identifier), "關聯搜尋紀錄 ID 無效。")
            searches[identifier] = ev.validate_search(context, manifest, identifier, source="supporting", repo=repo)
        coverage[repo] = {"repo": repo, "status": status, "reason": reason, "evidence": evidence, "searches": ids}
    require(set(coverage) == set(catalog) - {"entry"}, "未列出所有關聯 Repo 的功能鏈判定。")
    links, graph = [], {r: set() for r in catalog}
    preview = manifest.get("merge_preview") or {}
    final = ("merge-preview", preview["tree_sha"]) if preview.get("status") == "clean" else ("head", manifest["head_sha"])
    for number, item in enumerate(draft["functional_chain"], 1):
        require(isinstance(item, dict), "功能鏈項目須為物件。")
        require(item.get("id") == f"F-{number:03}", "功能鏈編號須連續。")
        left, right = item.get("from_repo"), item.get("to_repo")
        require(isinstance(left, str) and isinstance(right, str) and left in catalog and right in catalog,
                "功能鏈 Repo 不在 catalog；缺少 Repo 請列 chain_gaps。")
        require(isinstance(item.get("kind"), str) and item["kind"] in KINDS
                and isinstance(item.get("status"), str) and item["status"] in {"reviewed", "not-reviewed"},
                "功能鏈類型或狀態無效。")
        link = {k: item[k] for k in ("id", "from_repo", "to_repo", "kind", "status")}
        link["description"] = nonempty(item.get("description"), "功能鏈行為", require)
        link["evidence"] = citations(item.get("evidence", []), manifest, context)
        require(all(coverage[r]["status"] != "unrelated" for r in {left, right} - {"entry"}), "功能鏈不能引用已判定無關的 Repo。")
        if link["status"] == "reviewed":
            require({left, right} <= {e["repo"] for e in link["evidence"]}, "功能鏈須附兩端固定版本證據。")
            if "entry" in {left, right}:
                require(any(e["repo"] == "entry" and (e["source"], e["ref"]) == final for e in link["evidence"]),
                           "功能鏈入口須引用最終受審版本。")
            graph[left].add(right)
            graph[right].add(left)
        else:
            link["reason"] = nonempty(item.get("reason"), "功能鏈未完成原因", require)
            complete = False
        links.append(link)
    # Traverse with a visited set: API/event cycles must terminate.
    reached, pending = set(), ["entry"]
    while pending:
        repo = pending.pop()
        if repo not in reached:
            reached.add(repo)
            pending.extend(graph[repo] - reached)
    require(all(item["status"] != "reviewed" or repo in reached for repo, item in coverage.items()),
               "已審查的關聯 Repo 必須連回入口功能鏈。")
    return {"supporting_coverage": list(coverage.values()), "functional_chain": links,
            "chain_gaps": draft["chain_gaps"], "chain_complete": complete,
            "supporting_searches": [{"id": identifier, "repo": record["repo"], "ref": record["ref"],
                                     "query": record["query"], "match_count": len(record["matches"])}
                                    for identifier, record in searches.items()]}
