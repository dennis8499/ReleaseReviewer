#!/usr/bin/env python3
"""Build a deterministic, standalone Skill ZIP and its SHA-256 checksum."""
from __future__ import annotations

import argparse
import ast
import hashlib
from pathlib import Path
import re
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED
TEXT_SUFFIXES = {".md", ".py", ".yaml", ".yml", ".json", ".txt", ".toml"}


def canonical_bytes(path: Path) -> bytes:
    raw = path.read_bytes()
    return raw.replace(b"\r\n", b"\n") if path.name == "VERSION" or path.suffix.lower() in TEXT_SUFFIXES else raw


def build_release(skill: Path, destination: Path) -> Path:
    version = (skill / "VERSION").read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)", version):
        raise ValueError("VERSION 必須為 X.Y.Z。")
    required = ("SKILL.md", "agents/openai.yaml", "references/review-rules.md",
                "scripts/git_review_context.py", "scripts/git_support.py",
                "scripts/evidence.py", "scripts/release_report.py", "scripts/review_session.py",
                "scripts/group_context.py", "scripts/chain_review.py", "scripts/resource_rules.py",
                "scripts/review_worklist.py", "references/evidence-workflow.md",
                "scripts/frozen_evidence.py",
                "scripts/git_network_lease.py", "scripts/review_runtime.py", "scripts/merge_parent_coverage.py",
                "references/group-review.md", "references/resource-checks.md")
    for relative in required:
        if not (skill / relative).is_file():
            raise ValueError(f"套件缺少必要檔案：{relative}")
    files = []
    for path in sorted(skill.rglob("*")):
        if path.is_symlink() or (getattr(path, "is_junction", lambda: False)()):
            raise ValueError(f"套件不能包含連結：{path}")
        if not path.is_file() or "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}:
            continue
        if path.suffix == ".py":
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        files.append(path)
    destination.mkdir(parents=True, exist_ok=True)
    archive = destination / f"release-reviewer-{version}.zip"
    with ZipFile(archive, "w", compression=ZIP_DEFLATED) as stream:
        for path in files:
            item = ZipInfo("release-reviewer/" + path.relative_to(skill).as_posix(), (1980, 1, 1, 0, 0, 0))
            item.compress_type = ZIP_DEFLATED
            item.external_attr = 0o100644 << 16
            stream.writestr(item, canonical_bytes(path))
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix(".zip.sha256").write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    return archive


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    root = Path(__file__).resolve().parents[1]
    parser.add_argument("--skill-dir", type=Path, default=root / "skills/release-reviewer")
    parser.add_argument("--output-dir", type=Path, default=root / "dist")
    args = parser.parse_args()
    print(build_release(args.skill_dir, args.output_dir))


if __name__ == "__main__":
    main()
