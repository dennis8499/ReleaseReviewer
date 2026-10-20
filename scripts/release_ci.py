#!/usr/bin/env python3
"""Record CI provenance and promote exact, fully validated GitHub artifacts.

This file is copied byte-for-byte to the five repositories. It has no runtime
dependency on another repository and never accepts PR or local test evidence.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import io
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile

SCHEMA = "release-ci-proof/v1"
MAX_AGE = dt.timedelta(days=7)
NUMERIC = r"(?:0|[1-9][0-9]*)"
PRERELEASE_ID = r"(?:0|[1-9]\d*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*)"
BUILD_ID = r"[0-9A-Za-z-]+"
SEMVER = re.compile(rf"{NUMERIC}\.{NUMERIC}\.{NUMERIC}(?:-({PRERELEASE_ID}(?:\.{PRERELEASE_ID})*))?(?:\+({BUILD_ID}(?:\.{BUILD_ID})*))?")
SPECS = {
    "GitlabWorkSpace": {"workflow": "release.yml", "version": "package.json", "canonical": "linux-py3.11-stable",
        "cells": {"linux-py3.11-stable": ["Linux", "3.11"], "windows-py3.11-1.90.0": ["Windows", "3.11"],
                  "windows-py3.11-stable": ["Windows", "3.11"], "windows-py3.14-stable": ["Windows", "3.14"]}},
    "MergeReviewer": {"workflow": "ci.yml", "version": "skills/merge-reviewer/VERSION", "canonical": "linux-py3.14",
        "cells": {f"{os_name}-py{py}": [system, py] for os_name, system in (("linux", "Linux"), ("windows", "Windows")) for py in ("3.10", "3.14")}},
    "ReleaseReviewer": {"workflow": "ci.yml", "version": "skills/release-reviewer/VERSION", "canonical": "linux-py3.14",
        "cells": {f"{os_name}-py{py}": [system, py] for os_name, system in (("linux", "Linux"), ("windows", "Windows")) for py in ("3.10", "3.14")}},
    "Megin": {"workflow": "knowledge-portability.yml", "version": "VERSION", "canonical": "linux-py3.13",
        "fixed_dependencies": {"merge-reviewer": "fc08cf5706334264f289633aa80ed6ee34a09911"},
        "cells": {"source-py3.13": ["Linux", "3.13"], "linux-py3.13": ["Linux", "3.13"], "windows-py3.13": ["Windows", "3.13"]}},
    "code-base-llm-wiki": {"workflow": "release.yml", "version": "VERSION", "canonical": "linux-py3.14",
        "cells": {"linux-py3.11": ["Linux", "3.11"], "linux-py3.14": ["Linux", "3.14"]}},
}


class ProofError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise ProofError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def utcnow():
    return dt.datetime.now(dt.timezone.utc)


def timestamp(value):
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(parsed.tzinfo is not None, "CI timestamps must include timezone")
    return parsed.astimezone(dt.timezone.utc)


def git(root, *arguments):
    result = subprocess.run(["git", "-C", str(root), *arguments], capture_output=True, check=True, timeout=60)
    return result.stdout.decode("utf-8").strip()


def git_blob(root, relative):
    return subprocess.run(["git", "-C", str(root), "show", "HEAD:" + relative], capture_output=True,
                          check=True, timeout=60).stdout


def tool_version(root, command):
    try:
        result = subprocess.run(command, cwd=root, capture_output=True, timeout=10, check=False)
        return result.stdout.decode("utf-8", errors="replace").strip() if result.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def runtime_details(root, repository):
    name, _ = config(repository)
    details = {"git": tool_version(root, ["git", "--version"]), "node": tool_version(root, ["node", "--version"]),
               "python": platform.python_version(), "packages": {}, "fixed_dependencies": {}}
    if name == "MergeReviewer":
        for package in ("behave", "PyYAML"):
            try:
                details["packages"][package] = importlib.metadata.version(package)
            except importlib.metadata.PackageNotFoundError:
                details["packages"][package] = None
    if name == "Megin" and (root / ".test-dependencies/merge-reviewer/.git").exists():
        details["fixed_dependencies"]["merge-reviewer"] = git(root / ".test-dependencies/merge-reviewer", "rev-parse", "HEAD")
    if name == "GitlabWorkSpace":
        filename = root / ".gitlab-workspace-validation/ci/extension-host.json"
        require(filename.is_file(), "Missing actual VS Code host preflight metadata")
        host = json.loads(filename.read_text(encoding="utf-8"))
        require(host.get("schema") == "gitlab-workspace-extension-host/v1", "Invalid VS Code host metadata")
        details["vscode"] = {"requested": host["requested"], "version": host["version"]}
        require(host["requested"] == os.environ.get("VSCODE_TEST_VERSION"), "VS Code preflight request differs from matrix")
    return details


def config(repository):
    require(bool(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository)), "Invalid GitHub repository")
    name = repository.split("/")[1]
    require(name in SPECS, "Unsupported repository")
    return name, SPECS[name]


def validate_workspace_versions(manifest, lock):
    locked = lock.get("packages", {}).get("", {})
    require(lock.get("version") == locked.get("version") == manifest.get("version"), "Workspace manifest/lock versions differ")
    require(lock.get("name") == locked.get("name") == manifest.get("name"), "Workspace manifest/lock names differ")


def validate_version(repository, version):
    name, _ = config(repository)
    if name == "GitlabWorkSpace":
        require(isinstance(version, str) and SEMVER.fullmatch(version), "Workspace version must use valid SemVer")
    else:
        require(isinstance(version, str) and re.fullmatch(rf"{NUMERIC}\.{NUMERIC}\.{NUMERIC}", version), "Stable X.Y.Z version required")
    return version


def release_is_prerelease(repository, version):
    validate_version(repository, version)
    return config(repository)[0] == "GitlabWorkSpace" and (version == "0.1.0" or SEMVER.fullmatch(version)[1] is not None)


def identity(root, repository):
    name, spec = config(repository)
    sha = git(root, "rev-parse", "HEAD")
    require(bool(re.fullmatch(r"[0-9a-f]{40}", sha)), "Invalid source SHA")
    paths = [".github/workflows/" + spec["workflow"], "scripts/release_ci.py"]
    paths += [p for p in ("package-lock.json", "requirements-dev.txt", "requirements-dev.lock") if (root / p).is_file()]
    version_text = (root / spec["version"]).read_text(encoding="utf-8").strip()
    version = json.loads(version_text)["version"] if spec["version"].endswith(".json") else version_text
    if name == "GitlabWorkSpace":
        validate_workspace_versions(json.loads(git_blob(root, "package.json")), json.loads(git_blob(root, "package-lock.json")))
    validate_version(repository, version)
    return {"repository": repository, "sha": sha, "tree": git(root, "rev-parse", "HEAD^{tree}"),
            "version": version, "workflow": ".github/workflows/" + spec["workflow"],
            "inputs": {p: digest(git_blob(root, p)) for p in paths}, "matrix": spec["cells"],
            "canonical": spec["canonical"], "fixed_dependencies": spec.get("fixed_dependencies", {})}


def asset_names(repository, version):
    name, _ = config(repository)
    return {
        "GitlabWorkSpace": [f"gitlab-workspace-{version}.vsix", f"gitlab-workspace-kit-{version}.zip", "SHA256SUMS"],
        "MergeReviewer": [f"merge-reviewer-{version}.zip"],
        "ReleaseReviewer": [f"release-reviewer-{version}.zip", f"release-reviewer-{version}.zip.sha256"],
        "Megin": ["megin-skills.zip"],
        "code-base-llm-wiki": ["codebase-llm-wiki-codex.zip", "codebase-llm-wiki-copilot.zip", "update-manifest.json", "SHA256SUMS"],
    }[name]


def validate_checksums(files):
    for name, data in files.items():
        if name != "SHA256SUMS" and not name.endswith(".sha256"):
            continue
        rows = {}
        for line in data.decode("utf-8").splitlines():
            match = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9][A-Za-z0-9_.+-]*)", line)
            require(match is not None and match[2] not in rows, "Malformed/duplicate checksum entry")
            rows[match[2]] = match[1]
        expected = set(files) - {name} if name == "SHA256SUMS" else {name.removesuffix(".sha256")}
        require(set(rows) == expected, "Checksum file inventory differs")
        for filename, expected_digest in rows.items():
            require(filename in files and digest(files[filename]) == expected_digest, "Checksum differs: " + filename)


def record(root, repository, cell, directory):
    source = identity(root, repository)
    _, spec = config(repository)
    require(cell in spec["cells"], "Unexpected matrix cell")
    actual = [platform.system(), f"{sys.version_info.major}.{sys.version_info.minor}"]
    require(actual == spec["cells"][cell], "Cell runner/Python differs from required matrix")
    require(os.environ.get("GITHUB_ACTIONS") == "true", "Only GitHub Actions may record proof")
    require(os.environ.get("GITHUB_SHA") == source["sha"], "Checkout is not the workflow SHA")
    git(root, "diff", "--exit-code", "HEAD", "--")
    require(not directory.exists(), "Proof directory already exists")
    directory.mkdir(parents=True)
    files = {}
    if cell == spec["canonical"]:
        for name in asset_names(repository, source["version"]):
            path = root / "dist" / name
            require(path.is_file() and not path.is_symlink(), "Missing canonical asset: " + name)
            data = path.read_bytes()
            (directory / name).write_bytes(data)
            files[name] = {"sha256": digest(data), "bytes": len(data)}
    runtime = runtime_details(root, repository)
    if source["fixed_dependencies"] and not cell.startswith("source-"):
        require(runtime["fixed_dependencies"] == source["fixed_dependencies"], "Fixed integration dependency checkout differs")
    proof = {"schema": SCHEMA, "identity": source, "cell": cell, "environment": actual,
             "python": platform.python_version(), "runner_image": os.environ.get("ImageVersion", ""),
             "runtime": runtime,
             "run_id": int(os.environ["GITHUB_RUN_ID"]), "run_attempt": int(os.environ["GITHUB_RUN_ATTEMPT"]),
             "event": os.environ["GITHUB_EVENT_NAME"], "ref": os.environ["GITHUB_REF"],
             "created_at": utcnow().isoformat(), "files": files}
    (directory / "release-proof.json").write_text(json.dumps(proof, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return proof


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        require(urllib.parse.urlparse(new_url).scheme == "https", "Refusing non-HTTPS redirect")
        redirected = super().redirect_request(request, fp, code, message, headers, new_url)
        if urllib.parse.urlparse(new_url).netloc != urllib.parse.urlparse(request.full_url).netloc:
            redirected.remove_header("Authorization")
        return redirected


class GitHub:
    def __init__(self, repository, token):
        self.repository = repository
        self.token = token
        self.opener = urllib.request.build_opener(SafeRedirect())

    def request(self, path, method="GET", data=None, raw=False, content_type=None):
        url = path if path.startswith("https://") else f"https://api.github.com/repos/{self.repository}/{path}"
        host = urllib.parse.urlparse(url).hostname
        require(host in {"api.github.com", "uploads.github.com"}, "Unexpected GitHub API host")
        release_asset = re.fullmatch(r"/repos/[^/]+/[^/]+/releases/assets/\d+", urllib.parse.urlparse(url).path)
        accept = "application/octet-stream" if raw and method == "GET" and release_asset else "application/vnd.github+json"
        headers = {"Accept": accept, "User-Agent": "release-ci-proof", "X-GitHub-Api-Version": "2022-11-28",
                   "Authorization": "Bearer " + self.token}
        if data is not None and not isinstance(data, bytes):
            data = json.dumps(data).encode()
            headers["Content-Type"] = "application/json"
        if content_type:
            headers["Content-Type"] = content_type
        request = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with self.opener.open(request, timeout=120) as response:
                result = response.read()
            return result if raw else json.loads(result) if result else None
        except urllib.error.HTTPError as error:
            if error.code == 404 and method == "GET":
                return None
            raise ProofError(f"GitHub {method} failed with HTTP {error.code}") from None

    def pages(self, path, key):
        rows, page = [], 1
        while True:
            separator = "&" if "?" in path else "?"
            result = self.request(f"{path}{separator}per_page=100&page={page}")
            if key == "assets" and re.fullmatch(r"releases/\d+/assets", path):
                require(isinstance(result, list), "Incomplete GitHub release asset list response")
                batch = result
            else:
                require(isinstance(result, dict) and isinstance(result.get(key), list), "Incomplete GitHub list response")
                batch = result[key]
            rows.extend(batch)
            if len(batch) < 100:
                return rows
            page += 1
            require(page <= 1000, "GitHub pagination limit exceeded")


def validate_run(run, source, *, fallback=False, current_run=None, tag=None, now=None):
    now = now or utcnow()
    require(run and run.get("head_sha") == source["sha"], "Run SHA differs")
    require(run.get("head_repository", {}).get("full_name") == source["repository"], "Run source repository differs")
    require(run.get("path", "").split("@", 1)[0] == source["workflow"], "Run workflow differs")
    age = now - timestamp(run["created_at"])
    require(dt.timedelta(minutes=-5) <= age <= MAX_AGE, "CI proof is expired or future-dated")
    if fallback:
        require(str(run["id"]) == str(current_run), "Fallback must use the current release run")
        require(run["event"] == "push" and run["head_branch"] == tag, "Fallback must be the matching pushed tag")
        require(run["status"] in {"in_progress", "completed"}, "Fallback run is not active/completed")
        require(run.get("conclusion") in {None, "success"}, "Fallback run failed")
    else:
        require(run["event"] in {"push", "workflow_dispatch"} and run["head_branch"] == "main", "Only main CI proof is trusted")
        require(run["status"] == "completed" and run["conclusion"] == "success", "CI run did not succeed")


def matrix_jobs(jobs, cells):
    result = {}
    for cell in cells:
        candidates = [j for j in jobs if j["name"].endswith("[" + cell + "]")]
        require(candidates, "Missing matrix cell: " + cell)
        latest = max(int(j.get("run_attempt", 1)) for j in candidates)
        candidates = [j for j in candidates if int(j.get("run_attempt", 1)) == latest]
        require(len(candidates) == 1, "Duplicate matrix cell: " + cell)
        job = candidates[0]
        require(job["status"] == "completed" and job["conclusion"] == "success", "Matrix cell did not succeed: " + cell)
        result[cell] = job
    return result


def unpack_artifact(raw):
    result = {}
    require(len(raw) <= 512 * 1024 * 1024, "Artifact archive too large")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        require(len(archive.infolist()) <= 20, "Unexpected artifact member count")
        total = 0
        for info in archive.infolist():
            name = info.filename
            require(not info.is_dir() and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+-]*", name), "Unsafe artifact member")
            require(name not in result and (info.external_attr >> 16) & 0o170000 != 0o120000, "Duplicate or linked artifact member")
            total += info.file_size
            require(total <= 512 * 1024 * 1024, "Expanded artifact too large")
            result[name] = archive.read(info)
    return result


def verify_artifacts(api, run, source, jobs, *, fallback=False):
    artifacts = api.pages(f"actions/runs/{run['id']}/artifacts", "artifacts")
    canonical = {}
    for cell, job in jobs.items():
        attempt = int(job.get("run_attempt", 1))
        name = f"release-ci-{source['sha']}-{cell}-{attempt}"
        matches = [a for a in artifacts if a["name"] == name]
        require(len(matches) == 1 and not matches[0]["expired"], "Missing/expired/duplicate artifact: " + cell)
        artifact = matches[0]
        provenance = artifact.get("workflow_run") or {}
        require(provenance.get("id") == run["id"] and provenance.get("head_sha") == source["sha"], "Artifact run provenance differs")
        raw = api.request(f"actions/artifacts/{artifact['id']}/zip", raw=True)
        require(isinstance(raw, bytes), "Artifact unavailable")
        require(artifact.get("digest") == "sha256:" + digest(raw), "Artifact archive digest differs/missing")
        files = unpack_artifact(raw)
        require("release-proof.json" in files, "Artifact has no proof")
        proof = json.loads(files.pop("release-proof.json"))
        require(proof.get("schema") == SCHEMA and proof.get("identity") == source, "Proof source/workflow/dependencies/matrix differs")
        require(proof.get("run_id") == run["id"] and proof.get("run_attempt") == attempt and proof.get("cell") == cell, "Proof run/cell differs")
        require(proof.get("environment") == source["matrix"][cell], "Proof environment differs")
        if source.get("fixed_dependencies") and not cell.startswith("source-"):
            require(proof.get("runtime", {}).get("fixed_dependencies") == source["fixed_dependencies"], "Proof integration dependency differs")
        require(proof.get("event") == run["event"] and proof.get("ref") == ("refs/tags/" + run["head_branch"] if fallback else "refs/heads/main"), "Proof event/ref differs")
        require(timestamp(run["created_at"]) - dt.timedelta(minutes=5) <= timestamp(proof["created_at"]) <= utcnow() + dt.timedelta(minutes=5), "Invalid proof timestamp")
        expected = asset_names(source["repository"], source["version"]) if cell == source["canonical"] else []
        require(set(files) == set(expected) == set(proof["files"]), "Artifact file inventory differs")
        for filename, data in files.items():
            require(proof["files"][filename] == {"sha256": digest(data), "bytes": len(data)}, "Release asset digest differs: " + filename)
        if cell == source["canonical"]:
            canonical = files
    require(canonical, "No canonical assets")
    validate_checksums(canonical)
    return canonical


def verified_run(api, run_id, source, **options):
    run = api.request(f"actions/runs/{run_id}")
    validate_run(run, source, **options)
    attempts = int(run.get("run_attempt", 1))
    require(1 <= attempts <= 100, "Unexpected workflow attempt count")
    # Jobs do not reliably expose run_attempt. Fetch each attempt explicitly;
    # successful cells from earlier attempts remain valid after failed-job retry.
    history = []
    for attempt in range(1, attempts + 1):
        for job in api.pages(f"actions/runs/{run_id}/attempts/{attempt}/jobs", "jobs"):
            history.append(dict(job, run_attempt=attempt))
    jobs = matrix_jobs(history, source["matrix"])
    return verify_artifacts(api, run, source, jobs, fallback=options.get("fallback", False))


def select(api, source):
    path = f"actions/workflows/{Path(source['workflow']).name}/runs?head_sha={source['sha']}&branch=main&status=success"
    for run in api.pages(path, "workflow_runs"):
        try:
            verified_run(api, run["id"], source)
            return str(run["id"])
        except (ProofError, KeyError, ValueError, zipfile.BadZipFile):
            continue
    return ""


def tag_commit(api, tag):
    ref = api.request("git/ref/tags/" + urllib.parse.quote(tag, safe=""))
    require(ref, "Release tag does not exist")
    obj = ref["object"]
    for _ in range(5):
        if obj["type"] == "commit":
            return obj["sha"]
        require(obj["type"] == "tag", "Tag does not point to a commit")
        obj = api.request("git/tags/" + obj["sha"])["object"]
    raise ProofError("Too many nested annotated tags")


def validate_tag_target(api, tag, source):
    require(tag == "v" + source["version"], "Tag/version mismatch")
    require(tag_commit(api, tag) == source["sha"], "Published tag/source conflict")
    comparison = api.request("compare/" + source["sha"] + "...main")
    require(comparison and comparison.get("merge_base_commit", {}).get("sha") == source["sha"],
            "Release source is no longer contained in current main")


def publish_assets(api, tag, source, files, *, proof_run_id=None):
    validate_checksums(files)
    validate_tag_target(api, tag, source)
    path = "releases/tags/" + urllib.parse.quote(tag, safe="")
    release = api.request(path)
    expected_prerelease = release_is_prerelease(source["repository"], source["version"])
    if release is None:
        require(proof_run_id, "New release requires its verified CI run ID")
        notes = api.request("releases/generate-notes", "POST", {"tag_name": tag, "target_commitish": source["sha"]})
        ci_url = f"https://github.com/{source['repository']}/actions/runs/{proof_run_id}"
        verification = ("\n\n## Verification\n\n"
                        f"- Source commit: `{source['sha']}`\n"
                        f"- Complete configured CI matrix: [run {proof_run_id}]({ci_url})\n"
                        "- Published bytes match the validated canonical artifacts.\n\n"
                        "| Asset | SHA-256 | Bytes |\n| --- | --- | ---: |\n" +
                        "".join(f"| `{name}` | `{digest(data)}` | {len(data)} |\n" for name, data in sorted(files.items())))
        release = api.request("releases", "POST", {"tag_name": tag, "target_commitish": source["sha"],
                              "name": tag, "body": notes["body"] + verification, "draft": True, "prerelease": expected_prerelease})
    require(bool(release.get("prerelease")) == expected_prerelease, "Existing release prerelease status conflicts with version")
    existing = api.pages(f"releases/{release['id']}/assets", "assets")
    require(len({a["name"] for a in existing}) == len(existing), "Duplicate release assets")
    require(set(a["name"] for a in existing) <= set(files), "Existing release has unexpected assets")
    if not release["draft"]:
        require(set(a["name"] for a in existing) == set(files), "Published release has missing assets; refusing mutation")
    for asset in existing:
        data = api.request(f"releases/assets/{asset['id']}", raw=True, content_type=None)
        # GitHub's asset endpoint needs octet-stream Accept for binary downloads.
        require(isinstance(data, bytes) and data == files[asset["name"]], "Existing release asset conflicts: " + asset["name"])
    for name in sorted(set(files) - {a["name"] for a in existing}):
        upload = release["upload_url"].split("{", 1)[0] + "?name=" + urllib.parse.quote(name, safe="")
        api.request(upload, "POST", files[name], content_type="application/octet-stream")
    if release["draft"]:
        release = api.request(f"releases/{release['id']}", "PATCH", {"draft": False})
    # Do not call success until the formal assets have been downloaded and compared.
    actual = api.pages(f"releases/{release['id']}/assets", "assets")
    require(len(actual) == len(files) and set(a["name"] for a in actual) == set(files), "Final release inventory differs")
    for asset in actual:
        data = api.request(f"releases/assets/{asset['id']}", raw=True)
        require(data == files[asset["name"]], "Public asset download differs: " + asset["name"])
    return {"tag": tag, "sha": source["sha"], "url": release["html_url"], "ci_run_id": proof_run_id,
            "assets": {name: {"sha256": digest(data), "bytes": len(data)} for name, data in files.items()}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["record", "select", "publish", "describe"])
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--cell")
    parser.add_argument("--artifact-dir", type=Path)
    parser.add_argument("--tag", default=os.environ.get("GITHUB_REF_NAME", ""))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--run-id", default="")
    parser.add_argument("--fallback-current-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "describe":
            result = SPECS[config(args.repository)[0]]
        elif args.command == "record":
            require(args.cell and args.artifact_dir, "record needs --cell and --artifact-dir")
            result = record(args.root, args.repository, args.cell, args.artifact_dir)
        else:
            source = identity(args.root, args.repository)
            require(args.tag == "v" + source["version"], "Tag/version mismatch")
            token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
            require(token, "GitHub token missing")
            api = GitHub(args.repository, token)
            if args.command == "select":
                validate_tag_target(api, args.tag, source)
                selected = select(api, source)
                if args.output:
                    with args.output.open("a", encoding="utf-8") as stream:
                        stream.write("reuse_run_id=" + selected + "\n")
                result = {"reuse_run_id": selected}
            else:
                fallback = not args.run_id and args.fallback_current_run
                run_id = args.run_id or os.environ.get("GITHUB_RUN_ID")
                require(run_id and (args.run_id or fallback), "No validated run selected")
                files = verified_run(api, run_id, source, fallback=fallback,
                                     current_run=os.environ.get("GITHUB_RUN_ID"), tag=args.tag)
                result = publish_assets(api, args.tag, source, files, proof_run_id=run_id)
                if args.output:
                    args.output.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(result, sort_keys=True))
        return 0
    except (ProofError, KeyError, ValueError, OSError, subprocess.SubprocessError, zipfile.BadZipFile) as error:
        print("release-ci: " + str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
