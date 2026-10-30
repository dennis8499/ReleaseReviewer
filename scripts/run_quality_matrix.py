#!/usr/bin/env python3
"""Run isolated, blind Codex review samples against source-only fixtures."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import random
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
import create_quality_fixture

MODELS = {"gpt-6.1-sol": "high", "gpt-6-astra": "high", "gpt-6-luna": "max"}
CONDITIONS = ("no-skill", "v0.9.0", "candidate")


def digest(path: Path) -> str:
    value = hashlib.sha256()
    for item in sorted(path.rglob("*")):
        if item.is_file() and "__pycache__" not in item.parts and item.suffix not in {".pyc", ".pyo"}:
            value.update(item.relative_to(path).as_posix().encode("utf-8") + b"\0")
            value.update(hashlib.sha256(item.read_bytes()).digest())
    return value.hexdigest()


def redact(raw: str, sentinels: list[str]) -> tuple[str, bool]:
    leaked = any(value and value in raw for value in sentinels)
    for value in sentinels:
        if value:
            raw = raw.replace(value, "[REDACTED FIXTURE SECRET]")
    return raw, leaked


def redact_local_paths(raw: str) -> str:
    """Remove machine-specific roots from durable benchmark artifacts."""
    replacements = [(str(ROOT.resolve()), "<WORKSPACE>")]
    for variable in ("TEMP", "TMP", "TMPDIR"):
        value = os.environ.get(variable)
        if value:
            replacements.append((value, "<TEMP>"))
    home = str(Path.home())
    replacements.extend(((home + r"\AppData\Local", "<USER_LOCAL_APPDATA>"),
                         (home + r"\AppData\Roaming", "<USER_ROAMING_APPDATA>"),
                         (home, "<USER_HOME>")))
    for path, label in sorted(replacements, key=lambda item: len(item[0]), reverse=True):
        variants = {path, path.replace("\\", "/"), json.dumps(path, ensure_ascii=False)[1:-1],
                    json.dumps(path.replace("\\", "/"), ensure_ascii=False)[1:-1]}
        for variant in sorted(variants, key=len, reverse=True):
            raw = raw.replace(variant, label)
    # Event streams can contain JSON-escaped paths (with repeated backslashes),
    # and fixture roots may live outside the current user's home directory.
    raw = re.sub(r"(?i)(?<![A-Z0-9+.-])[A-Z]:(?:\\+|/+)[^\\/\"'\s]+(?:(?:\\+|/+)[^\\/\"'\s]+)*",
                 "<LOCAL_PATH>", raw)
    user_name = Path.home().name
    if user_name:
        raw = re.sub(re.escape(user_name), "<USER>", raw, flags=re.IGNORECASE)
    return raw


def _static_report_body(body: str) -> str:
    start_marker = "<!-- release-checklist:start -->"
    end_marker = "<!-- release-checklist:end -->"
    if body.count(start_marker) != 1 or body.count(end_marker) != 1:
        raise ValueError("Release report must have one delimited human checklist")
    start, end = body.index(start_marker), body.index(end_marker)
    if start >= end:
        raise ValueError("Release checklist markers are out of order")
    return body[:start] + body[end + len(end_marker):]


def redact_report_paths(report: str) -> str:
    """Redact local paths in a report and keep its embedded integrity metadata valid."""
    marker = re.search(r"<!-- release-review-report:([A-Za-z0-9+/=]+) -->", report)
    if not marker:
        return redact_local_paths(report)
    body = redact_local_paths(report[:marker.start()])
    suffix = redact_local_paths(report[marker.end():])
    metadata = json.loads(base64.b64decode(marker.group(1)))

    def scrub(value):
        if isinstance(value, str):
            return redact_local_paths(value)
        if isinstance(value, list):
            return [scrub(item) for item in value]
        if isinstance(value, dict):
            return {key: scrub(item) for key, item in value.items()}
        return value

    metadata = scrub(metadata)
    if "static_body_sha256" in metadata:
        metadata["static_body_sha256"] = hashlib.sha256(_static_report_body(body).encode("utf-8")).hexdigest()
    payload = json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    encoded = base64.b64encode(payload).decode("ascii")
    return body + f"<!-- release-review-report:{encoded} -->" + suffix


def write_text_exact(path: Path, text: str) -> tuple[str, int]:
    """Write UTF-8 bytes without Windows newline translation and return their identity."""
    payload = text.encode("utf-8")
    path.write_bytes(payload)
    return hashlib.sha256(payload).hexdigest(), len(payload)


def prompt_for(condition: str, workspace: Path, project: Path, report_dir: Path) -> str:
    common = f"""Review the committed change for release readiness.
Repo: {project}
Base branch: main
Head branch: feature/review
Workspace boundary: {workspace}
When starting the helper, pass the Repo path exactly as printed above; do not replace it with a display name or basename.
Write one Traditional Chinese Markdown report under {report_dir}.
Do not change source files. Do not run tests, deploy, access Git Server, call GitLab or inspect any oracle.
"""
    if condition == "no-skill":
        return common + "Identify only serious issues that can block this release; explain evidence and impact. Mention deployment prerequisites that a maintainer should verify."
    return common + "Load and follow the installed release-reviewer skill. Use its session helpers and produce its single report with an editable deployment checklist."


def codex_command(executable: str, args: list[str]) -> list[str]:
    if os.name == "nt":
        return [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/s", "/c",
                subprocess.list2cmdline([executable, *args])]
    return [executable, *args]


def stop_process_tree(process: subprocess.Popen) -> str | None:
    if os.name == "nt":
        try:
            result = subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                    capture_output=True, timeout=15, check=False)
            if result.returncode and process.poll() is None:
                return result.stderr.decode("utf-8", errors="replace")[-1000:]
            return None
        except Exception as exc:
            return f"{type(exc).__name__}: {exc}"
    try:
        process.kill()
        return None
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"


def completed_job(workspace: Path, reports: list[Path]) -> dict:
    roots = (workspace / ".rr" / "jobs", workspace / ".release-review-runtime" / "jobs")
    expected = {os.path.normcase(str(path.resolve())) for path in reports}
    candidates = []
    for root in roots:
        for path in root.glob("*.json") if root.is_dir() else ():
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            report = job.get("report_path")
            job["_report_matches"] = (isinstance(report, str)
                                      and os.path.normcase(str(Path(report).resolve())) in expected)
            candidates.append(job)
    matching = [item for item in candidates if item["_report_matches"]]
    selected = max(matching or candidates, key=lambda item: item.get("updated_at", ""), default={})
    if selected:
        selected = {key: value for key, value in selected.items() if key != "_report_matches"}
    return selected


def run_sample(*, model: str, effort: str, condition: str, case: str, run_number: int,
               scratch: Path, output: Path, baseline_skill: Path | None, candidate_skill: Path,
               executable: str, timeout: int) -> dict:
    sample_id = f"{model}-{condition}-{case}-{run_number:02}"
    sample_root = scratch / sample_id
    fixture_root = sample_root / "fixture"
    fixture = create_quality_fixture.create(fixture_root, case)
    workspace, project = Path(fixture["workspace"]), Path(fixture["project"])
    oracle_path = fixture_root / "oracle.json"
    oracle = json.loads(oracle_path.read_text(encoding="utf-8"))
    installed = None
    if condition != "no-skill":
        source = baseline_skill if condition == "v0.9.0" else candidate_skill
        if source is None:
            raise ValueError("baseline skill directory required for v0.9.0 samples")
        installed = workspace / ".agents" / "skills" / "release-reviewer"
        shutil.copytree(source, installed)
    report_dir = project / "review-reports"
    report_dir.mkdir(exist_ok=True)
    last_message = workspace / ".quality-matrix-last-message.txt"
    event_log = output / "events" / f"{sample_id}.jsonl"
    error_log = output / "events" / f"{sample_id}.stderr.txt"
    saved_report = output / "reports" / f"{sample_id}.md"
    event_log.parent.mkdir(parents=True, exist_ok=True)
    saved_report.parent.mkdir(parents=True, exist_ok=True)
    prompt = prompt_for(condition, workspace, project, report_dir)
    args = ["exec", "--model", model, "--config", f"model_reasoning_effort={effort}",
            "--approve-for-me", "--ephemeral", "--ignore-user-config", "--skip-git-repo-check",
            "--json", "--output-last-message", str(last_message), "--cd", str(workspace), "-"]
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "NO_COLOR": "1"}
    started = time.perf_counter()
    error = None
    return_code = None
    stdout = stderr = ""
    try:
        process = subprocess.Popen(codex_command(executable, args), stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
                                   cwd=workspace, creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        try:
            stdout_bytes, stderr_bytes = process.communicate(input=prompt.encode("utf-8"), timeout=timeout)
        except subprocess.TimeoutExpired:
            kill_error = stop_process_tree(process)
            try:
                stdout_bytes, stderr_bytes = process.communicate(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill()
                stdout_bytes, stderr_bytes = process.communicate()
            error = f"timeout after {timeout} seconds"
            if kill_error:
                error += f"; process-tree cleanup: {kill_error}"
        return_code = process.returncode
        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    elapsed = time.perf_counter() - started
    reports = sorted(report_dir.glob("*.md")) if report_dir.exists() else []
    report_text = reports[-1].read_text(encoding="utf-8", errors="replace") if reports else ""
    job = completed_job(workspace, reports)
    sentinels = oracle.get("secret_sentinels", [])
    raw_session_error = job.get("error") if isinstance(job.get("error"), str) else ""
    raw_cleanup_error = job.get("cleanup_error") if isinstance(job.get("cleanup_error"), str) else ""
    all_output = "\n".join((stdout, stderr, report_text, error or "", raw_cleanup_error,
                           last_message.read_text(encoding="utf-8", errors="replace") if last_message.exists() else "",
                           raw_session_error))
    _, secret_leak = redact(all_output, sentinels)
    sanitized_stdout, _ = redact(stdout, sentinels)
    sanitized_stderr, _ = redact(stderr, sentinels)
    sanitized_report, _ = redact(report_text, sentinels)
    sanitized_error, _ = redact(error or "", sentinels)
    sanitized_cleanup_error, _ = redact(raw_cleanup_error, sentinels)
    sanitized_stdout = redact_local_paths(sanitized_stdout)
    sanitized_stderr = redact_local_paths(sanitized_stderr)
    sanitized_report = redact_report_paths(sanitized_report)
    sanitized_error = redact_local_paths(sanitized_error)
    sanitized_cleanup_error = redact_local_paths(sanitized_cleanup_error)
    sanitized_session_error, _ = redact(raw_session_error, sentinels)
    sanitized_session_error = redact_local_paths(sanitized_session_error)
    write_text_exact(event_log, sanitized_stdout)
    write_text_exact(error_log, sanitized_stderr)
    report_sha256 = report_bytes = None
    if reports:
        report_sha256, report_bytes = write_text_exact(saved_report, sanitized_report)
    report_text_lower = report_text.lower()
    network = job.get("network_activity")
    outcome = job.get("review_outcome") or {}
    context_dir = job.get("context_dir")
    cleanup_verified = (not Path(context_dir).exists() and not job.get("cleanup_pending")
                        and not job.get("cleanup_error")) if isinstance(context_dir, str) else None
    result = {"id": sample_id, "model": model, "reasoning_effort": effort, "condition": condition,
              "case": case, "run": run_number, "elapsed_seconds": elapsed,
              "model_tokens": None, "agent_seconds": None, "http_requests": None, "ssh_requests": None,
              "exit_code": return_code, "error": sanitized_error or None, "report_created": bool(reports),
              "session_error": sanitized_session_error or None,
              "job_updated_at": job.get("updated_at"),
              "report_path": (Path("reports") / saved_report.name).as_posix() if reports else None,
              "report_sha256": report_sha256, "report_bytes": report_bytes, "secret_leak": secret_leak,
              "job_state": job.get("state"), "cleanup_verified": cleanup_verified,
               "cleanup_error": sanitized_cleanup_error or None, "network_activity": network,
              "network_log": job.get("network_log"), "runtime_metrics": job.get("metrics"),
              "review_complete": outcome.get("review_complete"),
              "review_status": outcome.get("review_status"),
              "release_assessment": outcome.get("release_assessment"),
              "blocker_count": outcome.get("blocker_count"),
              "pending_count": outcome.get("pending_count"), "gap_count": outcome.get("gap_count"),
              "oracle_expected": oracle["expected"], "semantic_score": None,
              "global_skill_isolation": "not proven; user CODEX_HOME remains active",
              "installed_skill_sha256": digest(installed) if installed else None,
              "signals": {key: bool(key in report_text_lower) for key in ("p0", "p1", "checklist", "ftp", "sftp", "kafka")}}
    last_message.unlink(missing_ok=True)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-skill-dir", type=Path, required=True)
    parser.add_argument("--candidate-skill-dir", type=Path, default=ROOT / "skills/release-reviewer")
    parser.add_argument("--model", action="append", choices=tuple(MODELS), dest="models")
    parser.add_argument("--condition", action="append", choices=CONDITIONS, dest="conditions")
    parser.add_argument("--case", action="append", choices=create_quality_fixture.CASES, dest="cases")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--seed", type=int, default=20261029)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--scratch-dir", type=Path)
    parser.add_argument("--codex", default=shutil.which("codex.cmd") or shutil.which("codex"))
    args = parser.parse_args()
    if args.runs < 1 or not args.codex:
        parser.error("positive --runs and a usable Codex CLI are required")
    baseline = args.baseline_skill_dir.resolve()
    candidate = args.candidate_skill_dir.resolve()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    # Keep the generated fixture path short enough for Windows nested runtime views.
    scratch = (args.scratch_dir or Path(tempfile.mkdtemp(prefix="q-"))).resolve()
    scratch.mkdir(parents=True, exist_ok=True)
    cases, models, conditions = (args.cases or list(create_quality_fixture.CASES),
                                 args.models or list(MODELS), args.conditions or list(CONDITIONS))
    matrix = [(model, conditions_item, case, run)
              for case in cases for run in range(1, args.runs + 1)
              for model in models for conditions_item in conditions]
    random.Random(args.seed).shuffle(matrix)
    metadata = {"schema": "ReleaseReviewerBlindQualityMatrix/v1", "complete": False,
                "acceptance_complete": False,
                "models": {model: MODELS[model] for model in models}, "conditions": conditions,
                "cases": cases, "runs_per_cell": args.runs, "seed": args.seed,
                "codex_cli": Path(args.codex.replace("\\", "/")).name, "baseline_skill_sha256": digest(baseline),
                "candidate_skill_sha256": digest(candidate),
                "no_skill_isolation_proven": False,
                "scoring": "blind reports are preserved; semantic_score requires an oracle-backed independent review"}
    samples = []
    result_path = output / "quality-matrix.json"
    for model, condition, case, number in matrix:
        sample = run_sample(model=model, effort=MODELS[model], condition=condition, case=case,
                            run_number=number, scratch=scratch, output=output,
                            baseline_skill=baseline, candidate_skill=candidate,
                            executable=args.codex, timeout=args.timeout)
        samples.append(sample)
        temporary = result_path.with_name(result_path.name + ".tmp")
        temporary.write_text(json.dumps({**metadata, "samples": samples}, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
        os.replace(temporary, result_path)
        print(json.dumps({"id": sample["id"], "exit_code": sample["exit_code"],
                          "report_created": sample["report_created"], "secret_leak": sample["secret_leak"]},
                         ensure_ascii=False), flush=True)
    metadata["complete"] = True
    temporary = result_path.with_name(result_path.name + ".tmp")
    temporary.write_text(json.dumps({**metadata, "samples": samples}, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")
    os.replace(temporary, result_path)
    summary = {}
    for model in models:
        for case in cases:
            cells = [row for row in samples if row["model"] == model and row["case"] == case]
            summary[f"{model}/{case}"] = {condition: {
                "runs": len(values), "median_seconds": statistics.median(row["elapsed_seconds"] for row in values),
                "reports_created": sum(row["report_created"] for row in values),
                "secret_leaks": sum(row["secret_leak"] for row in values), "semantic_score": None}
                for condition in conditions
                for values in ([row for row in cells if row["condition"] == condition],)}
    (output / "quality-summary.json").write_text(
        json.dumps({"schema": "ReleaseReviewerBlindQualitySummary/v1", "semantic_scoring_complete": False,
                    "cells": summary}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if all(row["exit_code"] == 0 and row["report_created"] and not row["secret_leak"] for row in samples) else 2


if __name__ == "__main__":
    raise SystemExit(main())
