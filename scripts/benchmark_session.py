#!/usr/bin/env python3
"""Compare the real session CLI on deterministic fixtures; this is not an agent benchmark."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
import support


def digest(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def payload_digest(skill):
    root = Path(skill).resolve()
    digest_value = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts and path.suffix not in {".pyc", ".pyo"}:
            digest_value.update(path.relative_to(root).as_posix().encode("utf-8") + b"\0")
            digest_value.update(hashlib.sha256(path.read_bytes()).digest())
    return digest_value.hexdigest()


def expand_response(value, context):
    if value.get("schema") == "ReleaseReviewEvidenceSummary/v1":
        raw = (context / "views" / (value["artifact"] + ".json")).read_bytes()
        if hashlib.sha256(raw).hexdigest() != value["artifact"]:
            raise ValueError("response artifact hash mismatch")
        value = json.loads(raw)
    if value.get("schema") == "ReleaseReviewStep/v2":
        contents = value["contents"]
        rows = []
        for row in value["operations"]:
            item = row["result"]
            if "content_id" in item:
                item = {**{k: v for k, v in item.items() if k != "content_id"},
                        **contents[item["content_id"]]}
            rows.append({**row, "result": item})
        value = {**value, "operations": rows}
    return value


def worker(skill, scenario, auto):
    base = Path(tempfile.mkdtemp(prefix="release-review-session-bench-")).resolve()
    result = {"success": False, "scenario": scenario, "stages": {}, "model_tokens": None,
              "agent_seconds": None, "http_requests": None, "ssh_requests": None,
              "expected_review_complete": True, "cleanup_error": None}
    peer_search_id = None
    try:
        repo = support.fixture(base)
        for number in range(24):
            support.write(repo, f"bench/file-{number:02}.py", (f"value_{number} = {number}\n" * 24))
        support.git(repo, "add", ".")
        support.git(repo, "commit", "-m", "session benchmark inputs")
        if scenario == "no-change":
            # Keep both branches on the same commit while retaining all read fixtures.
            support.git(repo, "update-ref", "refs/heads/main", "refs/heads/feature")
        if scenario == "group":
            peer = support.init_repo(base, "consumer")
            for number in range(24):
                support.write(peer, f"bench/file-{number:02}.py", (f"peer_{number} = {number}\n" * 24))
            support.git(peer, "add", ".")
            support.git(peer, "commit", "-m", "supporting inputs")
        script = Path(skill) / "scripts" / "review_session.py"
        env = support.command_env()

        def invoke(stage, arguments, context=None):
            # Keep Git's effective environment identical across rounds so the
            # persistent search-cache identity is not accidentally changed.
            trace = base / "git-trace.jsonl"
            previous_size = trace.stat().st_size if trace.exists() else 0
            stage_env = {**env, "GIT_TRACE2_EVENT": str(trace)}
            started = time.perf_counter()
            completed = subprocess.run([sys.executable, "-X", "utf8", "-B", str(script), *map(str, arguments)],
                                       capture_output=True, env=stage_env)
            elapsed = time.perf_counter() - started
            count = 0
            if trace.exists():
                with trace.open("rb") as stream:
                    stream.seek(previous_size)
                    lines = stream.read().decode("utf-8").splitlines()
                for line in lines:
                    try:
                        count += json.loads(line).get("event") == "start"
                    except json.JSONDecodeError:
                        pass
            result["stages"][stage] = {"wall_seconds": elapsed, "git_commands": count,
                                       "tool_calls": 1, "output_bytes": len(completed.stdout) + len(completed.stderr),
                                       "stdout_bytes": len(completed.stdout), "stderr_bytes": len(completed.stderr),
                                       "exit_code": completed.returncode}
            if completed.returncode:
                diagnostic = (completed.stderr + b"\n" + completed.stdout).decode("utf-8", errors="replace")
                raise RuntimeError(f"{stage} exited {completed.returncode}: {diagnostic[-5000:]}")
            output = json.loads(completed.stdout.decode("utf-8"))
            if output.get("schema") == "ReleaseReviewEvidenceSummary/v1":
                context = context or Path(output["context_dir"])
                return expand_response(output, context)
            return output

        start = invoke("start", ["start", "--workspace", base, "--project", repo, "--base", "main", "--head", "feature"])
        context, runtime, identifier = Path(start["context_dir"]), start["runtime_root"], start["request_id"]
        result["network_activity"] = start["network_activity"]
        manifest = json.loads((context / "manifest.json").read_text(encoding="utf-8"))
        peer_id = next((row["id"] for row in manifest["repositories"] if row["role"] == "supporting"), None)
        common = ["--runtime-root", runtime, "--request-id", identifier]
        evidence_rows = []
        for round_number in range(2):
            operations = []
            for n in range(24):
                if scenario == "group" and n % 2:
                    row = {"repo": peer_id, "source": "supporting", "path": f"bench/file-{round_number * 12 + n // 2:02}.py"}
                else:
                    file_number = round_number * 12 + n // 2
                    source = ("head" if round_number == 0 else "merge-preview") if scenario == "group" else ("head" if n % 2 == 0 else "merge-preview")
                    row = {"repo": "entry", "source": source,
                           "path": f"bench/file-{file_number:02}.py"}
                operations.append({"id": f"round-{round_number}-{n}", "op": "read", **row})
            operations.append({"id": f"round-{round_number}-base-search", "op": "search", "repo": "entry",
                               "source": "base", "query": "EXISTING_TIMEOUT"})
            if scenario == "group" and round_number == 1:
                operations.append({"id": "peer-search", "op": "search", "repo": peer_id,
                                   "source": "supporting", "query": "peer_3"})
            requests = context / f"benchmark-round-{round_number}.json"
            requests.write_text(json.dumps(operations), encoding="utf-8")
            response = invoke("step-" + str(round_number), ["step", *common, "--requests-file", requests,
                              *(["--response-format", "auto"] if auto else [])], context)
            response = expand_response(response, context)
            result["stages"]["step-" + str(round_number)]["search_activity"] = response.get("search_activity")
            if not response["complete"]:
                raise ValueError("step did not complete")
            if scenario == "group" and round_number == 1:
                peer_row = next((row for row in response["operations"] if row["id"] == "peer-search"), None)
                peer_search_id = (peer_row or {}).get("result", {}).get("search_id")
                if not peer_search_id:
                    raise ValueError("group fixture did not save its whole-Repo peer search")
            evidence_rows.extend(row["result"] for row in response["operations"])
        draft = support.draft_for(manifest)
        if scenario == "no-change":
            for row in draft["dependency_coverage"]:
                row.update(status="not-applicable",
                           reason="固定 base/head 及乾淨合併預覽證明本次沒有新增差異。", evidence=[])
        if peer_id:
            draft["supporting_coverage"] = [{
                "repo": peer_id, "status": "unrelated",
                "reason": "合成 benchmark 的 peer Repo 刻意建立為與入口 Repo 無契約關係。",
                "searches": [peer_search_id], "evidence": [],
            }]
        (context / "review-result-draft.json").write_text(json.dumps(draft, ensure_ascii=False), encoding="utf-8")
        check = invoke("check", ["check", *common], context)
        finish = invoke("finish", ["finish", *common], context)
        outcome = finish.get("review_outcome") if isinstance(finish.get("review_outcome"), dict) else {}
        blocker_count = finish.get("blocker_count", outcome.get("blocker_count"))
        pending_count = finish.get("pending_count", outcome.get("pending_count"))
        gap_count = finish.get("gap_count", outcome.get("gap_count"))
        result.update(evidence_digest=digest(evidence_rows), evidence_count=len(evidence_rows),
                      check_passed=check.get("precheck_passed"), review_complete=finish.get("review_complete"),
                      review_status=finish.get("review_status", outcome.get("review_status")),
                      blocker_count=blocker_count, pending_count=pending_count, gap_count=gap_count,
                      report_published=finish.get("publication_complete"), cleanup_pending=finish.get("cleanup_pending", False),
                      runtime_metrics=finish.get("metrics"), repair_seconds=0.0,
                      total_seconds=sum(stage["wall_seconds"] for stage in result["stages"].values()))
        result["success"] = bool(check.get("precheck_passed") is True
                                  and finish.get("publication_complete") is True
                                  and finish.get("review_complete") is True
                                  and blocker_count == 0
                                  and gap_count == 0
                                  and not finish.get("cleanup_pending", False))
        if not result["success"]:
            result["error"] = "benchmark completed without the expected complete, blocker-free review and cleanup"
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        try:
            support.review_session._remove_tree(base)
        except Exception as exc:
            result["success"] = False
            result["cleanup_error"] = f"{type(exc).__name__}: {exc}"
    return result


def helper_target_ratio(scenario):
    """Helper-only session runs may regress by at most five percent."""
    if scenario not in {"repository", "group", "no-change"}:
        raise ValueError(f"unknown benchmark scenario: {scenario}")
    return 1.05


def validate_output_path(output, payload_dirs):
    """Keep a partial results file from mutating either measured payload."""
    if output is None:
        return None
    resolved_output = Path(output).resolve()
    for payload_dir in payload_dirs:
        root = Path(payload_dir).resolve()
        if resolved_output == root or root in resolved_output.parents:
            raise ValueError("benchmark output must be outside the baseline and candidate payload directories")
    return resolved_output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-skill-dir", type=Path)
    parser.add_argument("--candidate-skill-dir", type=Path,
                        help="Use an unpacked release payload instead of the source checkout.")
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--scenario", action="append", choices=("repository", "group", "no-change"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--skill", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--auto", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        value = worker(args.skill, args.scenario[0], args.auto)
        print(json.dumps(value, ensure_ascii=False))
        return 0 if value["success"] else 2
    if not args.baseline_skill_dir or args.runs < 1:
        parser.error("--baseline-skill-dir and positive --runs are required")
    candidate_skill = args.candidate_skill_dir or ROOT / "skills/release-reviewer"
    try:
        args.output = validate_output_path(args.output, (args.baseline_skill_dir, candidate_skill))
    except ValueError as exc:
        parser.error(str(exc))
    environment = {"platform": platform.platform(), "python": sys.version,
                   "baseline_payload_sha256": payload_digest(args.baseline_skill_dir),
                   "candidate_payload_sha256": payload_digest(candidate_skill)}
    samples = []
    scenarios = args.scenario or ("repository", "group", "no-change")
    for scenario in scenarios:
        for number in range(args.runs):
            order = ("baseline", "candidate") if number % 2 == 0 else ("candidate", "baseline")
            for variant in order:
                skill = args.baseline_skill_dir if variant == "baseline" else candidate_skill
                proc = subprocess.run([sys.executable, "-X", "utf8", "-B", __file__, "--worker", "--skill", str(skill),
                                       "--scenario", scenario, *(["--auto"] if variant == "candidate" else [])],
                                      capture_output=True, encoding="utf-8", errors="replace")
                try:
                    sample = json.loads(proc.stdout)
                except ValueError:
                    sample = {"success": False, "scenario": scenario, "error": proc.stderr[-3000:]}
                samples.append({"variant": variant, "run": number + 1, **sample})
                if args.output:
                    args.output.parent.mkdir(parents=True, exist_ok=True)
                    partial = {"schema": "ReleaseReviewSessionBenchmark/v1", "complete": False,
                               "environment": environment, "samples": samples}
                    temporary = args.output.with_name(args.output.name + ".tmp")
                    temporary.write_text(json.dumps(partial, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                    os.replace(temporary, args.output)
    summary = {}
    for scenario in scenarios:
        items = [item for item in samples if item["scenario"] == scenario]
        valid = len(items) == args.runs * 2 and all(item["success"] for item in items)
        equivalent = valid and len({item["evidence_digest"] for item in items}) == 1
        target_ratio = helper_target_ratio(scenario)
        report = {"success": False, "all_samples_succeeded": valid, "evidence_equivalent": equivalent,
                  "helper_target_ratio": target_ratio}
        if equivalent:
            for variant in ("baseline", "candidate"):
                runs = [item for item in items if item["variant"] == variant]
                report[variant] = {"total_seconds": statistics.median(item["total_seconds"] for item in runs),
                                   "step_seconds": statistics.median(sum(item["stages"][f"step-{i}"]["wall_seconds"] for i in range(2)) for item in runs),
                                   "step_git_commands": statistics.median(sum(item["stages"][f"step-{i}"]["git_commands"] for i in range(2)) for item in runs)}
            report["total_ratio"] = report["candidate"]["total_seconds"] / report["baseline"]["total_seconds"]
            report["step_ratio"] = report["candidate"]["step_seconds"] / report["baseline"]["step_seconds"]
            stage_names = sorted(set.intersection(*(set(item["stages"]) for item in items)))
            report["stage_medians_seconds"] = {
                variant: {stage: statistics.median(item["stages"][stage]["wall_seconds"]
                                                    for item in items if item["variant"] == variant)
                          for stage in stage_names}
                for variant in ("baseline", "candidate")}
            report["target_met"] = report["total_ratio"] <= report["helper_target_ratio"]
            report["success"] = bool(valid and equivalent and report["target_met"])
        report["pair_count"] = args.runs
        summary[scenario] = report
    unchanged = (environment["baseline_payload_sha256"] == payload_digest(args.baseline_skill_dir)
                 and environment["candidate_payload_sha256"] == payload_digest(candidate_skill))
    if not unchanged:
        for report in summary.values():
            report["success"] = False
            report["payload_changed_during_run"] = True
    value = {"schema": "ReleaseReviewSessionBenchmark/v1", "complete": True,
             "scope": "Synthetic helper CLI only; not an agent or semantic-quality measurement.",
             "method": "Alternating independent processes; start, two 24-read steps with searches, check and finish; no-change uses identical base/head; all samples retained.",
             "environment": environment,
             "summary": summary, "samples": samples}
    text = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0 if all(item["success"] for item in summary.values()) else 2


if __name__ == "__main__":
    raise SystemExit(main())
