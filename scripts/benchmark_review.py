#!/usr/bin/env python3
"""Compare frozen helper workflows; this does not measure an agent or an LLM."""
from __future__ import annotations

import argparse
from contextlib import contextmanager, redirect_stderr, redirect_stdout
import copy
import hashlib
import importlib
import io
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ("no-diff", "normal", "repeated-citations", "dense-search", "group")
STAGES = ("capture", "evidence", "validate", "publish")
DENSE_LINES = 25000
REPETITIONS = 12


def normalize(value, replacements=()):
    """Remove run-specific locations/times while retaining review semantics."""
    if isinstance(value, dict):
        return {key: normalize(item, replacements) for key, item in value.items()
                if key != "generated_at"}
    if isinstance(value, list):
        return [normalize(item, replacements) for item in value]
    if isinstance(value, str):
        for original, replacement in replacements:
            value = value.replace(str(original), replacement)
            value = value.replace(str(original).replace("\\", "/"), replacement)
        return value
    return value


def semantic_digest(value, replacements=()):
    data = json.dumps(normalize(value, replacements), ensure_ascii=False,
                      sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


class Measurements:
    def __init__(self):
        self.stages = {name: dict(wall_seconds=0.0, git_subprocesses=0,
                                  helper_commands=0, output_bytes=0, artifact_bytes=0)
                       for name in STAGES}
        self.active = None

    @contextmanager
    def stage(self, name):
        previous = self.active
        self.active = name
        start = time.perf_counter()
        try:
            yield
        finally:
            self.stages[name]["wall_seconds"] += time.perf_counter() - start
            self.active = previous

    @contextmanager
    def count_git(self):
        original = subprocess.run

        def measured(command, *args, **kwargs):
            if (self.active is not None and isinstance(command, (list, tuple))
                    and Path(str(command[0])).name.lower() in {"git", "git.exe"}):
                self.stages[self.active]["git_subprocesses"] += 1
            return original(command, *args, **kwargs)

        subprocess.run = measured
        try:
            yield
        finally:
            subprocess.run = original

    def command(self, main, argv):
        stdout, stderr = io.StringIO(), io.StringIO()
        self.stages[self.active]["helper_commands"] += 1
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(argv)
        raw = stdout.getvalue()
        self.stages[self.active]["output_bytes"] += len(raw.encode("utf-8"))
        if code != 0:
            raise RuntimeError(f"{main.__module__} exited {code}: {stderr.getvalue().strip()}")
        return json.loads(raw)

    def total(self):
        return {field: sum(stage[field] for stage in self.stages.values())
                for field in next(iter(self.stages.values()))}


def citations(value):
    if isinstance(value, dict):
        if {"repo", "source", "ref", "path"} <= value.keys():
            yield value
        else:
            for item in value.values():
                yield from citations(item)
    elif isinstance(value, list):
        for item in value:
            yield from citations(item)


def repeat_citations(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"evidence", "definition_evidence"} and isinstance(item, list):
                value[key] = copy.deepcopy(item) * REPETITIONS
            else:
                repeat_citations(item)
    elif isinstance(value, list):
        for item in value:
            repeat_citations(item)


def create_fixture(scenario, support):
    if scenario == "group":
        case = importlib.import_module("test_group_resources").GroupTests()
        case.setUp()
        return case
    case = support.RepoTest()
    if scenario != "dense-search":
        case.setUp()
        return case
    # Put dense matches in the common baseline, avoiding an artificial merge
    # or extra changed file solely to set up this search workload.
    case.root = Path(tempfile.mkdtemp(prefix="release-reviewer-benchmark-")).resolve()
    case.repo = support.init_repo(case.root)
    case.contexts = []
    for path, content in support.BASE_FILES.items():
        support.write(case.repo, path, content)
    support.write(case.repo, "dense.txt", "".join(f"DENSE_MATCH_{number:05}\n" for number in range(DENSE_LINES)))
    support.git(case.repo, "add", ".")
    support.git(case.repo, "commit", "-m", "baseline with dense matches")
    support.git(case.repo, "switch", "-c", "feature")
    for path, content in support.HEAD_FILES.items():
        support.write(case.repo, path, content)
    support.git(case.repo, "add", ".")
    support.git(case.repo, "commit", "-m", "release changes")
    return case


def run_evidence(measurements, evidence, context, requests):
    if hasattr(evidence, "batch"):
        output = measurements.command(evidence.main, ["batch", "--context-dir", str(context),
                                       "--requests", json.dumps(requests, ensure_ascii=False)])
        if not output.get("complete") or not all(item.get("ok") for item in output["results"]):
            raise RuntimeError("Evidence batch was incomplete.")
        return [item["result"] for item in output["results"]]
    results = []
    for request in requests:
        args = [request["action"], "--context-dir", str(context)]
        for key in ("source", "repo", "ref", "path", "query"):
            if key in request:
                args += ["--" + key, request[key]]
        if request.get("ignore_case"):
            args.append("--ignore-case")
        results.append(measurements.command(evidence.main, args))
    return results


def worker(skill_dir, scenario):
    """A fresh process imports only one variant before loading shared fixtures."""
    sys.path.insert(0, str(Path(skill_dir).resolve() / "scripts"))
    # support.py prepends the current scripts directory. Preloading all helpers
    # here keeps its fixture utilities bound to this process's chosen variant.
    helpers = {name: importlib.import_module(name) for name in
               ("git_support", "group_context", "review_session", "evidence", "chain_review",
                "resource_rules", "git_review_context", "release_report")}
    sys.path.insert(0, str(ROOT / "tests"))
    support = importlib.import_module("support")
    os.environ.update(support.command_env())
    measured = Measurements()
    case = None
    outcome = {"success": False, "scenario": scenario}
    try:
        case = create_fixture(scenario, support)
        capture, evidence, report = (helpers[name] for name in
                                     ("git_review_context", "evidence", "release_report"))
        with measured.count_git():
            with measured.stage("capture"):
                args = ["--workspace", str(case.root if scenario == "group" else case.repo),
                        "--project", str(case.repo), "--base", "main", "--head",
                        "main" if scenario == "no-diff" else "feature"]
                if scenario == "group":
                    args += ["--workspace-context-json", json.dumps(case.hints)]
                manifest = measured.command(capture.main, args)
                context = Path(manifest["context_dir"])
                case.contexts.append(context)
            with measured.stage("evidence"):
                if scenario == "group":
                    draft, _ = case.group_draft(context, manifest)
                else:
                    draft = support.draft_for(manifest)
                    if scenario != "no-diff":
                        draft["code_risks"] = [support.risk_item(manifest)]
                        draft["config_changes"] = [support.config_item(context, manifest)]
                        draft["db_changes"] = [support.db_item(context, manifest)]
                if scenario == "repeated-citations":
                    repeat_citations(draft)
                requests = [{"action": "list", "source": "base"}]
                requests += [{"action": "read", **{key: proof[key] for key in ("repo", "source", "ref", "path")}}
                             for proof in citations(draft)]
                if scenario == "dense-search":
                    requests.append({"action": "search", "source": "base", "query": "DENSE_MATCH_"})
                results = run_evidence(measured, evidence, context, requests)
                if scenario == "dense-search" and len(results[-1]["matches"]) != DENSE_LINES:
                    raise RuntimeError("Dense search lost matches.")
                draft_path = support.save_draft(context, draft)
            validated = []
            original_validate = report.validate_result

            def timed_validate(*args, **kwargs):
                with measured.stage("validate"):
                    result = original_validate(*args, **kwargs)
                validated.append(result)
                return result

            report.validate_result = timed_validate
            try:
                with measured.stage("publish"):
                    output = measured.command(report.main, ["--context-dir", str(context),
                                                            "--result", str(draft_path)])
                    markdown = Path(output["markdown_report"])
                    measured.stages["publish"]["artifact_bytes"] = markdown.stat().st_size
            finally:
                # Publish includes validation. Remove that nested time so stage
                # totals count the actual pipeline once, without a second check.
                measured.stages["publish"]["wall_seconds"] -= measured.stages["validate"]["wall_seconds"]
                report.validate_result = original_validate
            if len(validated) != 1:
                raise RuntimeError("Publishing must validate exactly once.")
        outcome.update(success=True, semantic_digest=semantic_digest(
            {"manifest": manifest, "evidence": results, "result": validated[0]},
            [(str(context), "<context>"), (str(case.root), "<fixture>")]),
            request_count=len(requests), dense_matches=DENSE_LINES if scenario == "dense-search" else 0)
    except Exception as exc:
        outcome["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if case is not None:
            try:
                case.tearDown()
            except Exception as exc:
                outcome.update(success=False, error=f"Fixture cleanup failed: {type(exc).__name__}: {exc}")
    outcome.update(stages=measured.stages, total=measured.total())
    return outcome


def launch_worker(skill_dir, scenario):
    try:
        result = subprocess.run([sys.executable, "-X", "utf8", "-B", str(Path(__file__).resolve()),
                                 "--worker", "--skill-dir", str(skill_dir), "--scenario", scenario],
                                capture_output=True, encoding="utf-8", errors="replace", timeout=600)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"success": False, "scenario": scenario, "error": f"Worker failed: {type(exc).__name__}: {exc}"}
    try:
        output = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"success": False, "scenario": scenario,
                "error": f"Worker exited {result.returncode}: {result.stderr.strip()}"}
    if result.returncode != 0:
        output.update(success=False, error=output.get("error", f"Worker exited {result.returncode}"))
    return output


def summarize(samples):
    """Never publish successful medians or speedups from partial/failed runs."""
    grouped = {}
    all_passed = True
    for sample in samples:
        grouped.setdefault(sample["scenario"], {"baseline": [], "current": []})[sample["variant"]].append(sample)
    summary = {}
    for scenario, variants in grouped.items():
        values = variants["baseline"] + variants["current"]
        successful = (bool(variants["baseline"]) and len(variants["baseline"]) == len(variants["current"])
                      and all(item.get("success") for item in values))
        digests = {item.get("semantic_digest") for item in values}
        equivalent = successful and len(digests) == 1 and None not in digests
        item = {"success": equivalent, "workers_succeeded": successful, "semantic_equivalent": equivalent}
        if equivalent:
            for variant, runs in variants.items():
                item[variant] = {"total": {field: statistics.median(run["total"][field] for run in runs)
                                           for field in runs[0]["total"]}}
                item[variant]["stages"] = {
                    stage: {field: statistics.median(run["stages"][stage][field] for run in runs)
                            for field in runs[0]["stages"][stage]} for stage in STAGES}
            before, after = (item[v]["total"]["wall_seconds"] for v in ("baseline", "current"))
            item["speedup"] = before / after if after else None
        all_passed = all_passed and equivalent
        summary[scenario] = item
    return {"success": bool(samples) and all_passed, "scenarios": summary}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-skill-dir", type=Path)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--scenario", choices=SCENARIOS, action="append")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--skill-dir", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.worker:
        if args.skill_dir is None or not args.scenario or len(args.scenario) != 1:
            parser.error("worker requires one skill directory and scenario")
        result = worker(args.skill_dir, args.scenario[0])
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["success"] else 2
    if args.baseline_skill_dir is None:
        parser.error("--baseline-skill-dir is required")
    if args.runs < 1:
        parser.error("--runs must be positive")
    current = ROOT / "skills/release-reviewer"
    for path in (args.baseline_skill_dir, current):
        if not (path / "scripts/git_review_context.py").is_file():
            parser.error(f"skill scripts are missing: {path}")
    samples = []
    for scenario in args.scenario or SCENARIOS:
        for number in range(args.runs):
            order = ("baseline", "current") if number % 2 == 0 else ("current", "baseline")
            for variant in order:
                sample = launch_worker(args.baseline_skill_dir if variant == "baseline" else current, scenario)
                samples.append({"variant": variant, "run": number + 1, **sample})
    result = {"schema": "ReleaseReviewHelperBenchmark/v1",
              "scope": "Helper workflow only; no LLM or agent end-to-end measurement.",
              "methodology": {"runs": args.runs, "summary": "median",
                  "processes": "One fresh process per variant, scenario and run; fixture setup and interpreter startup excluded.",
                  "helper_commands": "In-process helper main() invocations; per-command Python startup is not measured.",
                  "stages": "Capture, evidence/draft preparation, one nested publish validation, and publish excluding that validation.",
                  "output_bytes": "UTF-8 bytes of captured helper stdout; report file bytes are artifact_bytes.",
                  "dense_lines": DENSE_LINES, "citation_repetitions": REPETITIONS},
              "summary": summarize(samples), "samples": samples}
    encoded = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    else:
        print(encoded, end="")
    return 0 if result["summary"]["success"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
