#!/usr/bin/env python3
"""Import independent, hash-bound semantic reviews and evaluate release gates."""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
from pathlib import Path
import statistics
import sys

import quality_isolation
import run_quality_matrix as runner

CRITERIA = ("no_false_blockers", "required_blockers_found", "coverage_honest",
            "required_gaps_preserved", "evidence_supported", "deployment_dependencies")


def evaluate(matrix, reviews, artifact_root, *, required_models, required_cases,
             required_conditions, minimum_runs=3):
    """Score actual reports. A completed helper run never supplies semantic scores."""
    if (not isinstance(matrix, dict) or not isinstance(reviews, dict)
            or matrix.get("schema") != "ReleaseReviewerBlindQualityMatrix/v1"
            or reviews.get("schema") != "ReleaseReviewerIndependentScores/v1"):
        raise ValueError("Unsupported matrix or independent-score schema.")
    if reviews.get("independent") is not True or not isinstance(reviews.get("reviewer"), str) or not reviews["reviewer"].strip():
        raise ValueError("An identified independent reviewer must attest to these scores.")
    samples = matrix.get("samples")
    entries = reviews.get("reviews")
    if not isinstance(samples, list) or not isinstance(entries, list):
        raise ValueError("Matrix samples and independent reviews must be arrays.")
    by_id, seen_cells = {}, set()
    for sample in samples:
        if not isinstance(sample, dict) or not isinstance(sample.get("id"), str) or sample["id"] in by_id:
            raise ValueError("Matrix samples need unique IDs.")
        cell = tuple(sample.get(key) for key in ("model", "condition", "case", "run"))
        if (not all(isinstance(value, str) for value in cell[:3]) or type(cell[3]) is not int or cell[3] < 1
                or sample["id"] != f"{cell[0]}-{cell[1]}-{cell[2]}-{cell[3]:02}" or cell in seen_cells):
            raise ValueError("Sample identity differs from its model/condition/case/run.")
        seen_cells.add(cell)
        by_id[sample["id"]] = sample
    scored = {}
    for review in entries:
        identifier = review.get("id") if isinstance(review, dict) else None
        if not isinstance(identifier, str) or identifier not in by_id or identifier in scored:
            raise ValueError("Independent review names an unknown or duplicate sample.")
        sample = by_id[identifier]
        if review.get("report_sha256") != sample.get("report_sha256") or not isinstance(review.get("report_sha256"), str):
            raise ValueError("Independent review is bound to a different report: " + identifier)
        criteria = review.get("criteria")
        if not isinstance(criteria, dict) or set(criteria) != set(CRITERIA) or any(type(value) is not bool for value in criteria.values()):
            raise ValueError("Each independent review must score all six criteria as booleans.")
        if not isinstance(review.get("rationale"), str) or not review["rationale"].strip():
            raise ValueError("Independent review needs a source/oracle-backed rationale.")
        scored[identifier] = {"reviewer": reviews["reviewer"].strip(), "criteria": criteria,
                              "rationale": review["rationale"].strip(),
                              "score": sum(criteria.values()) / len(CRITERIA)}
    reasons = []
    if matrix.get("complete") is not True:
        reasons.append("Matrix execution is incomplete.")
    runs = matrix.get("runs_per_cell")
    if type(runs) is not int or runs < minimum_runs:
        reasons.append(f"At least {minimum_runs} independent runs per cell are required.")
        runs = minimum_runs
    expected = set(itertools.product(required_models, required_conditions, required_cases, range(1, runs + 1)))
    if seen_cells != expected:
        reasons.append(f"Required matrix cells differ: {len(expected - seen_cells)} missing, {len(seen_cells - expected)} unexpected.")
    output_samples, integrity_complete = [], True
    root = Path(artifact_root).resolve()
    for sample in samples:
        identifier = sample["id"]
        semantic = scored.get(identifier)
        path = sample.get("report_path")
        report = (root / path).resolve() if isinstance(path, str) else None
        valid_report = report is not None and report.is_relative_to(root) and report.is_file()
        valid_report = bool(valid_report and hashlib.sha256(report.read_bytes()).hexdigest() == sample.get("report_sha256"))
        if not valid_report:
            reasons.append(identifier + ": saved report is missing or changed.")
            integrity_complete = False
        if (sample.get("exit_code") != 0 or sample.get("report_created") is not True
                or sample.get("secret_leak") is not False or sample.get("error")):
            reasons.append(identifier + ": execution/report/secret integrity gate failed.")
            integrity_complete = False
        proof = sample.get("skill_isolation")
        if not quality_isolation.proof_valid(proof) or (sample["condition"] == "no-skill" and proof.get("expected_skill_key") is not None):
            reasons.append(identifier + ": enabled-skill isolation is unproven.")
            integrity_complete = False
        if sample["condition"] != "no-skill" and (sample.get("job_state") != "published"
                or sample.get("cleanup_verified") is not True or sample.get("report_receipt_verified") is not True):
            reasons.append(identifier + ": reviewer publication/cleanup gate failed.")
            integrity_complete = False
        if sample["condition"] != "no-skill":
            expected_payload = matrix.get("candidate_skill_sha256" if sample["condition"] == "candidate" else "baseline_skill_sha256")
            if (not isinstance(proof, dict) or proof.get("expected_skill_key") is None
                    or not isinstance(expected_payload, str)
                    or sample.get("installed_skill_sha256") != expected_payload
                    or sample.get("installed_skill_sha256_after") != expected_payload):
                reasons.append(identifier + ": selected skill payload is unproven or changed.")
                integrity_complete = False
        if semantic is None:
            reasons.append(identifier + ": independent semantic review is missing.")
        elif sample["condition"] == "candidate" and not all(semantic["criteria"].values()):
            reasons.append(identifier + ": candidate has a critical semantic failure.")
        output_samples.append({**sample, "semantic_score": semantic["score"] if semantic else None,
                               "independent_review": semantic})
    scoring_complete = bool(samples) and len(scored) == len(samples)
    complete = (matrix.get("complete") is True and scoring_complete and seen_cells == expected
                and matrix.get("runs_per_cell", 0) >= minimum_runs and integrity_complete)
    cells = {}
    for model, case in itertools.product(required_models, required_cases):
        conditions = {}
        for condition in required_conditions:
            rows = [row for row in output_samples if (row["model"], row["case"], row["condition"]) == (model, case, condition)]
            judged = [row for row in rows if row["independent_review"] is not None]
            all_scored = len(rows) == runs and len(judged) == runs
            conditions[condition] = {"runs": len(rows), "scored_runs": len(judged),
                "mean_score": statistics.mean(row["semantic_score"] for row in judged) if all_scored else None,
                "criterion_pass_rates": {key: statistics.mean(row["independent_review"]["criteria"][key] for row in judged)
                    if all_scored else None for key in CRITERIA}}
        candidate_score = conditions.get("candidate", {}).get("mean_score")
        deltas = {}
        for condition in required_conditions:
            if condition == "candidate":
                continue
            reference_score = conditions[condition]["mean_score"]
            deltas[condition] = candidate_score - reference_score if candidate_score is not None and reference_score is not None else None
        cells[f"{model}/{case}"] = {"conditions": conditions, "candidate_score_delta": deltas}
    return {**matrix, "samples": output_samples, "semantic_scoring_complete": scoring_complete,
            "acceptance_complete": complete, "acceptance_passed": complete and not reasons,
            "acceptance_reasons": reasons, "semantic_cells": cells}


def score_files(matrix_path, scores_path, *, required_models=None, required_cases=None,
                required_conditions=None, minimum_runs=3):
    raw = Path(matrix_path).read_bytes()
    matrix = json.loads(raw.decode("utf-8-sig"))
    reviews = json.loads(Path(scores_path).read_text(encoding="utf-8-sig"))
    checksum = hashlib.sha256(raw).hexdigest()
    if not isinstance(reviews, dict) or reviews.get("matrix_sha256") != checksum:
        raise ValueError("Independent scores refer to a different matrix snapshot.")
    result = evaluate(matrix, reviews, Path(matrix_path).parent,
        required_models=tuple(runner.MODELS) if required_models is None else required_models,
        required_cases=runner.create_quality_fixture.CASES if required_cases is None else required_cases,
        required_conditions=runner.CONDITIONS if required_conditions is None else required_conditions,
        minimum_runs=minimum_runs)
    return {**result, "scoring_matrix_sha256": checksum,
            "independent_scores_sha256": hashlib.sha256(Path(scores_path).read_bytes()).hexdigest()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matrix", type=Path, required=True)
    parser.add_argument("--scores", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.output.resolve() in (args.matrix.resolve(), args.scores.resolve()):
            raise ValueError("Scored output must preserve the original matrix and independent review file.")
        result = score_files(args.matrix, args.scores)
        with args.output.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps({key: result[key] for key in ("semantic_scoring_complete", "acceptance_complete", "acceptance_passed")}))
        return 0 if result["acceptance_passed"] else 3
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({"error": runner.redact_local_paths(str(exc))}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
