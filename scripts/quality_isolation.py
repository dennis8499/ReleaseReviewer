"""Prove the CLI's enabled skill inventory using a dedicated benchmark profile.

No model turn is started by the inventory probe. Authentication is supplied by
the caller's environment or by logging in to the dedicated profile separately.
"""
from __future__ import annotations

from collections import deque
import hashlib
import json
import os
from pathlib import Path
import queue
import shutil
import stat
import subprocess
import threading
import time

PROFILE_MARKER = ".release-quality-profile.json"


def linked(path):
    try:
        info = Path(path).lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def command(executable, args):
    selected = Path(shutil.which(str(executable)) or executable)
    if os.name == "nt" and selected.suffix.lower() in {".cmd", ".bat"}:
        # Call npm's Node launcher directly: cmd.exe would interpret metacharacters
        # in workspace/output arguments even though they are data, not commands.
        launcher = selected.resolve().parent / "node_modules/@openai/codex/bin/codex.js"
        node = shutil.which("node.exe")
        if not launcher.is_file() or not node:
            raise ValueError("Use a native Codex executable or an npm Codex installation with Node available.")
        return [node, str(launcher), *args]
    return [executable, *args]


def profile_environment(profile):
    requested = Path(profile).absolute()
    if linked(requested):
        raise ValueError("Quality profile cannot be a link.")
    profile = requested.resolve()
    current = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).resolve()
    if profile in (current, (Path.home() / ".codex").resolve()) or profile.is_symlink():
        raise ValueError("Use a dedicated quality profile, separate from the user's Codex home.")
    profile.mkdir(parents=True, exist_ok=True)
    marker = profile / PROFILE_MARKER
    if linked(marker):
        raise ValueError("Quality profile marker cannot be a link.")
    expected = {"schema": "ReleaseQualityProfile/v1", "directory": str(profile)}
    if marker.exists():
        saved = json.loads(marker.read_text(encoding="utf-8"))
        if any(saved.get(key) != value for key, value in expected.items()):
            raise ValueError("Quality profile ownership differs.")
        config = profile / "config.toml"
        if (not config.is_file() or linked(config)
                or hashlib.sha256(config.read_bytes()).hexdigest() != saved.get("config_sha256")):
            raise ValueError("Quality profile config changed outside the harness.")
    else:
        if (profile / "config.toml").exists():
            raise ValueError("An existing unowned Codex config cannot become a quality profile.")
        write_profile(profile, [])
    return {**os.environ, "CODEX_HOME": str(profile), "PYTHONIOENCODING": "utf-8", "NO_COLOR": "1"}


def write_profile(profile, selectors):
    profile = Path(profile)
    lines = ['[history]', 'persistence = "none"', '', '[skills.bundled]', 'enabled = false', '']
    for selector in selectors:
        lines += ['[[skills.config]]', *(f"{key} = {json.dumps(value, ensure_ascii=True)}"
                  for key, value in selector.items() if key != "enabled"),
                  f"enabled = {'true' if selector['enabled'] else 'false'}", '']
    config = "\n".join(lines).encode("utf-8")
    target = profile / "config.toml"
    if linked(target) or linked(profile / PROFILE_MARKER):
        raise ValueError("Quality profile files cannot be links.")
    target.write_bytes(config)
    (profile / PROFILE_MARKER).write_bytes(canonical({"schema": "ReleaseQualityProfile/v1",
        "directory": str(profile.resolve()), "config_sha256": hashlib.sha256(config).hexdigest()}))


def skill_inventory(executable, workspace, env, *, timeout=30):
    """Use the documented initialize/skills/list protocol without starting a turn."""
    messages, stderr_tail = queue.Queue(), deque(maxlen=20)
    process = subprocess.Popen(command(executable, ["app-server", "--listen", "stdio://"]),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        cwd=workspace, env=env, text=True, encoding="utf-8", errors="replace", bufsize=1,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

    def read_stdout():
        try:
            for line in process.stdout:
                try:
                    messages.put(json.loads(line))
                except ValueError:
                    pass
        finally:
            messages.put(None)

    def read_stderr():
        for line in process.stderr:
            stderr_tail.append(line[:500])

    readers = [threading.Thread(target=read_stdout, daemon=True), threading.Thread(target=read_stderr, daemon=True)]
    for reader in readers:
        reader.start()
    deadline = time.monotonic() + timeout

    def send(value):
        process.stdin.write(json.dumps(value, ensure_ascii=False) + "\n")
        process.stdin.flush()

    def receive(identifier):
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Skill inventory timed out.")
            try:
                message = messages.get(timeout=remaining)
            except queue.Empty as exc:
                raise TimeoutError("Skill inventory timed out.") from exc
            if message is None:
                raise ValueError("Skill inventory server closed before responding: " + "".join(stderr_tail)[-1000:])
            if message.get("id") == identifier:
                if "error" in message:
                    raise ValueError("Skill inventory RPC failed: " + str(message["error"]))
                return message.get("result")

    try:
        send({"id": 0, "method": "initialize", "params": {
            "clientInfo": {"name": "release_quality", "version": "1.0"},
            "capabilities": {"experimentalApi": True}}})
        receive(0)
        send({"method": "initialized"})
        send({"id": 1, "method": "skills/list", "params": {"cwds": [str(Path(workspace).resolve())], "forceReload": True}})
        result = receive(1)
        data = result.get("data") if isinstance(result, dict) else None
        if not isinstance(data, list) or len(data) != 1 or data[0].get("errors"):
            raise ValueError("Skill inventory is incomplete or contains discovery errors.")
        skills = data[0].get("skills")
        if (not isinstance(skills, list) or any(not isinstance(row, dict)
                or not isinstance(row.get("name"), str) or type(row.get("enabled")) is not bool for row in skills)):
            raise ValueError("Skill inventory has an unsupported shape.")
        return [{key: row.get(key) for key in ("name", "path", "enabled", "scope")} for row in skills]
    finally:
        process.stdin.close()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                               capture_output=True, timeout=15, check=False)
            else:
                process.kill()
            process.wait(timeout=5)
        for reader in readers:
            reader.join(timeout=2)
        process.stdout.close()
        process.stderr.close()


def verify_inventory(skills, allowed_skill):
    expected = set() if allowed_skill is None else {os.path.normcase(str(Path(allowed_skill).resolve()))}
    actual = set()
    for row in skills:
        if row["enabled"]:
            if not isinstance(row.get("path"), str):
                return False
            actual.add(os.path.normcase(str(Path(row["path"]).resolve())))
    return actual == expected


def prepare_isolation(executable, workspace, profile, allowed_skill=None):
    env = profile_environment(profile)
    initial = skill_inventory(executable, workspace, env)
    selectors = [{"path": row["path"], "enabled": False} if isinstance(row.get("path"), str)
                 else {"name": row["name"], "enabled": False} for row in initial]
    if allowed_skill is not None:
        selectors.append({"path": str(Path(allowed_skill).resolve()), "enabled": True})
    write_profile(profile, selectors)
    before = skill_inventory(executable, workspace, env)
    if not verify_inventory(before, allowed_skill):
        raise ValueError("Skill isolation failed: enabled inventory differs from this condition.")
    evidence = {"schema": "ReviewerSkillIsolation/v1", "proven": False,
                "config_sha256": hashlib.sha256((Path(profile) / "config.toml").read_bytes()).hexdigest(),
                "expected_skill": str(allowed_skill) if allowed_skill else None,
                "before": before, "after": None}
    return env, evidence


def finish_isolation(executable, workspace, profile, env, evidence):
    profile_environment(profile)  # Reject config changes before trusting the second inventory.
    actual_hash = hashlib.sha256((Path(profile) / "config.toml").read_bytes()).hexdigest()
    after = skill_inventory(executable, workspace, env)
    return {**evidence, "after": after,
            "proven": actual_hash == evidence["config_sha256"] and verify_inventory(after, evidence["expected_skill"])}


def public_evidence(evidence):
    """Keep stable inventory identities without exposing local user paths."""
    def key(path):
        return hashlib.sha256(os.path.normcase(str(Path(path).resolve())).encode("utf-8")).hexdigest()
    result = {name: evidence.get(name) for name in ("schema", "proven", "config_sha256")}
    result["expected_skill_key"] = key(evidence["expected_skill"]) if evidence.get("expected_skill") else None
    for name in ("before", "after"):
        rows = evidence.get(name)
        result[name] = [{"name": row["name"], "enabled": row["enabled"], "scope": row.get("scope"),
                        "path_key": key(row["path"]) if isinstance(row.get("path"), str) else None}
                       for row in rows] if isinstance(rows, list) else None
    return result


def proof_valid(proof):
    if not isinstance(proof, dict) or proof.get("schema") != "ReviewerSkillIsolation/v1" or proof.get("proven") is not True:
        return False
    checksum = proof.get("config_sha256")
    if not isinstance(checksum, str) or len(checksum) != 64 or any(c not in "0123456789abcdef" for c in checksum):
        return False
    expected = proof.get("expected_skill_key")
    if expected is not None and (not isinstance(expected, str) or len(expected) != 64):
        return False
    for field in ("before", "after"):
        rows = proof.get(field)
        if not isinstance(rows, list):
            return False
        if any(not isinstance(row, dict) or type(row.get("enabled")) is not bool for row in rows):
            return False
        active = [row.get("path_key") for row in rows if row["enabled"]]
        if any(not isinstance(value, str) for value in active) or set(active) != (set() if expected is None else {expected}):
            return False
    return True


def main(argv=None):
    import argparse
    import shutil
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--skill", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--codex", default=shutil.which("codex.cmd") or shutil.which("codex"))
    args = parser.parse_args(argv)
    if not args.codex or not args.workspace.is_dir():
        parser.error("A usable Codex CLI and source workspace are required")
    env, evidence = prepare_isolation(args.codex, args.workspace, args.profile, args.skill)
    proof = public_evidence(finish_isolation(args.codex, args.workspace, args.profile, env, evidence))
    result = {"schema": "ReviewerSkillIsolationProbe/v1", "model_reviews_executed": 0, "skill_isolation": proof}
    with args.output.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"proven": proof_valid(proof), "model_reviews_executed": 0}))
    return 0 if proof_valid(proof) else 2


if __name__ == "__main__":
    raise SystemExit(main())
