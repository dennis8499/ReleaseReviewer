"""Own complete fixed Git trees; evidence never borrows a source repository."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re

import git_support as git

POLICY = "frozen-start/v1"
RECEIPT_POLICY = "snapshot-validation-receipt/v1"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def file_sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def receipt_inputs(context, record):
    """Hash the complete snapshot shape and contents, including Git controls."""
    root = repository(context, record)
    files, directories = {}, []
    pending = [root]
    while pending:
        directory = pending.pop()
        for item in sorted(directory.iterdir(), key=lambda value: value.name):
            relative = item.relative_to(root).as_posix()
            owned_path(context, record["snapshot"]["git_dir"] + "/" + relative)
            require(not item.is_symlink() and not getattr(item, "is_junction", lambda: False)(),
                    "固定快照不能包含連結。")
            if item.is_dir():
                directories.append(relative)
                pending.append(item)
            else:
                require(item.is_file(), "固定快照包含不支援的檔案類型。")
                files[relative] = file_sha256(item)
    description = {key: value for key, value in record.items() if key != "validation_receipt"}
    raw = json.dumps(description, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {"schema": RECEIPT_POLICY, "record_sha256": hashlib.sha256(raw).hexdigest(),
            "directories": sorted(directories), "files": dict(sorted(files.items()))}


def receipt_digest(receipt):
    raw = json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def verify_receipt(context, record, expected=None):
    receipt = record.get("validation_receipt")
    require(isinstance(receipt, dict) and receipt.get("schema") == RECEIPT_POLICY,
            "固定快照驗證收據無效。")
    if expected is not None:
        require(isinstance(expected, str) and receipt_digest(receipt) == expected,
                "固定快照驗證收據與 request 不符。")
    require(receipt_inputs(context, record) == receipt, "固定快照或來源描述與驗證收據不符。")


def validate_for_step(context, record, expected=None):
    """Reuse only a request-bound full validation; old contexts remain full checks."""
    if expected is None:
        return validate(context, record)
    return verify_receipt(context, record, expected)


def require_supported_root(path):
    # Git for Windows setup.c rejects absolute GIT_DIR > PATH_MAX - 40 bytes.
    # Keep the supported boundary explicit instead of failing midway through
    # capture or changing the requested runtime/source after a failure.
    if os.name == "nt" and len(str(path).encode("utf-8")) > 220:
        raise git.ReviewContextError("Windows Git owned snapshot 路徑限制：Git 根目錄須不超過 220 UTF-8 bytes；"
                                     "此路徑為 " + str(len(str(path).encode("utf-8"))) + " bytes。")


def owned_path(context: Path, relative: str) -> Path:
    require(git.safe_relative_git_path(relative) and "\\" not in relative, "快照路徑無效。")
    root = Path(context).resolve()
    path = root / relative
    for parent in (path, *path.parents):
        if parent == root:
            break
        require(not parent.is_symlink() and not getattr(parent, "is_junction", lambda: False)(),
                "快照路徑不能包含連結。")
    require(path.resolve().is_relative_to(root), "快照路徑超出 owned context。")
    return path


def repository(context: Path, record: dict) -> Path:
    snapshot = record.get("snapshot")
    require(isinstance(snapshot, dict) and snapshot.get("policy") == POLICY, "缺少自足固定快照。")
    relative = f"repos/{record['id']}/snapshot.git"
    require(snapshot.get("git_dir") == relative, "快照 Repo 身分不符。")
    path = owned_path(context, relative)
    require(path.is_dir() and owned_path(context, relative + "/HEAD").is_file(), "固定 Git 快照不存在。")
    require(owned_path(context, relative + "/objects").is_dir(), "固定物件目錄不存在。")
    require(not (path / "objects/info/alternates").exists(), "固定快照不能借用其他物件。")
    pack = owned_path(context, relative + "/objects/pack")
    require(pack.is_dir(), "固定 pack 目錄不存在。")
    for item in pack.iterdir():
        owned_path(context, relative + "/objects/pack/" + item.name)
    owned_path(context, relative + "/config")
    return path


def environment(context: Path, record: dict) -> dict:
    path = repository(context, record)
    result = dict(os.environ)
    for key in ("GIT_DIR", "GIT_COMMON_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE",
                "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES"):
        result.pop(key, None)
    result.update(GIT_DIR=str(path), GIT_COMMON_DIR=str(path), GIT_OBJECT_DIRECTORY=str(path / "objects"),
                  GIT_ALTERNATE_OBJECT_DIRECTORIES="", GIT_NO_REPLACE_OBJECTS="1", GIT_NO_LAZY_FETCH="1",
                  GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0", GIT_ALLOW_PROTOCOL="")
    return result


def inventory(repo: Path, tree: str, env=None) -> tuple[list, set]:
    raw = git.run_git(repo, ["ls-tree", "-r", "-t", "-z", tree], binary=True, env=env)
    entries, objects = [], {tree}
    for entry in raw.split(b"\0"):
        if not entry:
            continue
        metadata, path = entry.split(b"\t", 1)
        mode, kind, object_id = metadata.decode("ascii").split()
        name = path.decode("utf-8", errors="surrogateescape")
        entries.append({"path": name, "mode": mode, "kind": kind, "object": object_id})
        if kind != "commit":  # A gitlink does not include the submodule's repository.
            objects.add(object_id)
    return entries, objects


def owned_capture_git(context, destination, args, *, workdir=None, **kwargs):
    """Keep unsupported Windows Git root depths explicit, without fallback."""
    try:
        return git.run_git(workdir or destination, args, **kwargs)
    except git.GitCommandError as exc:
        if os.name != "nt" or not any(text in exc.stderr.lower() for text in
                ("filename too long", "$git_dir too big", "not a git repository")):
            raise
        paths = {"context": context, "store": destination}
        lengths = ", ".join(name + "=" + str(len(str(path).encode("utf-16-le")) // 2)
                            + " UTF-16/" + str(len(str(path).encode("utf-8"))) + " UTF-8 bytes"
                            for name, path in paths.items())
        raise git.ReviewContextError("Windows Git 無法建立此 owned snapshot 路徑（" + lengths + "）；"
                                     "此 Git 版本的路徑限制不能由 core.longpaths 完全解除。原始錯誤：" + str(exc)) from exc


def seal(context: Path, record: dict, refs: list[str], *, env=None, extra_commit_objects=()):
    source = Path(record["path"])
    relative = f"repos/{record['id']}/snapshot.git"
    destination = owned_path(context, relative)
    require_supported_root(destination)
    object_format = str(git.run_git(source, ["rev-parse", "--show-object-format"])).strip()
    require(object_format in {"sha1", "sha256"}, "不支援的 Git 物件格式。")
    owned_capture_git(context, destination, ["init", "--bare", f"--object-format={object_format}", str(destination)], workdir=context)
    objects, sources, instructions, trees, instruction_content = set(), {}, [], {}, {}
    for reference in dict.fromkeys(refs):
        require(isinstance(reference, str) and re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", reference), "固定 ref 無效。")
        tree = str(git.run_git(source, ["rev-parse", f"{reference}^{{tree}}"], env=env)).strip()
        if tree not in trees:
            trees[tree] = inventory(source, tree, env)
        entries, closure = trees[tree]
        sources[reference] = {"tree": tree, "entries": entries}
        objects.update(closure)
        objects.add(reference)
        for item in entries:
            if item["kind"] == "blob" and PurePosixPath(item["path"]).name == "AGENTS.md":
                if item["object"] not in instruction_content:
                    instruction_content[item["object"]] = git.run_git(source, ["cat-file", "blob", item["object"]], binary=True, env=env)
                content = instruction_content[item["object"]]
                instructions.append({"ref": reference, "path": item["path"],
                                     "sha256": hashlib.sha256(content).hexdigest()})
    extra = list(dict.fromkeys(extra_commit_objects))
    require(all(isinstance(reference, str) and re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", reference)
                for reference in extra), "祖先證明 commit 身分無效。")
    if extra:
        checked = str(git.run_git(source, ["cat-file", "--batch-check"],
                                 input_bytes=("\n".join(extra) + "\n").encode("ascii"), env=env)).splitlines()
        require(len(checked) == len(extra) and all(len(row.split()) == 3 and row.split()[0] == reference
                and row.split()[1] == "commit" and row.split()[2].isdigit()
                for reference, row in zip(extra, checked)), "祖先證明只能封存 commit 物件。")
        objects.update(extra)  # Commit bodies only; their historical trees are not captured.
    record["snapshot"] = {"policy": POLICY, "git_dir": relative, "object_format": object_format,
                          "captured_at": datetime.now(timezone.utc).isoformat(), "sources": sources}
    record["instructions"] = instructions
    # Git for Windows can reject long absolute pack finalization paths even
    # with core.longpaths. Stream into the unchanged owned directory and use
    # short basenames; the manifest binds the checksum and both full hashes.
    temporary = destination / "objects/pack/.snapshot.tmp"
    try:
        with temporary.open("xb") as output:
            git.run_git(source, ["pack-objects", "--stdout"], output_file=output,
                        input_bytes=("\n".join(sorted(objects)) + "\n").encode("ascii"), env=env)
        checksum_bytes = 20 if object_format == "sha1" else 32
        with temporary.open("rb") as packed:
            require(packed.read(4) == b"PACK", "固定 pack 標頭無效。")
            packed.seek(-checksum_bytes, os.SEEK_END)
            checksum = packed.read(checksum_bytes).hex()
        name = "snapshot"
        os.replace(temporary, destination / "objects/pack" / (name + ".pack"))
        indexed = str(owned_capture_git(context, destination, ["index-pack", "-o", "objects/pack/" + name + ".idx", "objects/pack/" + name + ".pack"],
                                  env=environment(context, record))).strip()
        require(indexed == checksum, "固定 pack checksum 與索引驗證結果不符。")
        record["snapshot"]["pack"] = {"file": "objects/pack/snapshot.pack", "index": "objects/pack/snapshot.idx",
                                       "checksum": checksum,
                                       "sha256": file_sha256(destination / "objects/pack/snapshot.pack"),
                                       "index_sha256": file_sha256(destination / "objects/pack/snapshot.idx")}
    finally:
        temporary.unlink(missing_ok=True)
    before = receipt_inputs(context, record)
    validate(context, record)
    after = receipt_inputs(context, record)
    require(before == after, "完整快照驗證期間來源或檔案已變動。")
    record["validation_receipt"] = after


def validate(context: Path, record: dict):
    try:
        return _validate(context, record)
    except (git.ReviewContextError, OSError) as exc:
        raise ValueError(f"固定快照驗證失敗：{exc}") from exc


def _validate(context: Path, record: dict):
    if "validation_receipt" in record:
        verify_receipt(context, record)
    path, env = repository(context, record), environment(context, record)
    packed = record["snapshot"].get("pack")
    if packed is not None:
        require(isinstance(packed, dict) and packed.get("file") == "objects/pack/snapshot.pack"
                and packed.get("index") == "objects/pack/snapshot.idx", "固定 pack 路徑無效。")
        checksum_length = {"sha1": 40, "sha256": 64}.get(record["snapshot"].get("object_format"))
        require(isinstance(packed.get("checksum"), str) and checksum_length is not None
                and re.fullmatch("[a-f0-9]{" + str(checksum_length) + "}", packed["checksum"]), "固定 pack checksum 無效。")
        for field, name in (("sha256", "file"), ("index_sha256", "index")):
            expected = packed.get(field)
            require(isinstance(expected, str) and re.fullmatch(r"[a-f0-9]{64}", expected), "固定 pack 檔案摘要無效。")
            target = owned_path(context, record["snapshot"]["git_dir"] + "/" + packed[name])
            require(file_sha256(target) == expected, "固定 pack 或 index 摘要不符。")
        with (path / packed["file"]).open("rb") as stream:
            stream.seek(-(checksum_length // 2), os.SEEK_END)
            require(stream.read().hex() == packed["checksum"], "固定 pack checksum 摘要不符。")
    sources = record["snapshot"].get("sources")
    require(isinstance(sources, dict) and sources, "快照沒有固定來源。")
    objects, checked_trees = set(), {}
    for reference, expected in sources.items():
        tree = str(git.run_git(path, ["rev-parse", f"{reference}^{{tree}}"], env=env)).strip()
        require(tree == expected["tree"], "快照 tree 身分不符。")
        if tree not in checked_trees:
            checked_trees[tree] = inventory(path, tree, env)
        entries, closure = checked_trees[tree]
        require(entries == expected["entries"], "快照內容清單不符。")
        objects.update(closure)
        objects.add(reference)
    checked = str(git.run_git(path, ["cat-file", "--batch-check"], env=env,
                              input_bytes=("\n".join(sorted(objects)) + "\n").encode("ascii")))
    require(len(checked.splitlines()) == len(objects) and all(len(row.split()) == 3 for row in checked.splitlines()),
            "快照物件不完整。")
    for index in (path / "objects/pack").glob("*.idx"):
        owned_path(context, record["snapshot"]["git_dir"] + "/" + index.relative_to(path).as_posix())
        git.run_git(path, ["verify-pack", index.relative_to(path).as_posix()], env=env)
    for item in record.get("instructions", []):
        content = git.run_git(path, ["cat-file", "blob", f"{item['ref']}:{item['path']}"], binary=True, env=env)
        require(hashlib.sha256(content).hexdigest() == item["sha256"], "固定審查指示摘要不符。")


def capture_workspace_instructions(context: Path, roots: list[Path], repositories: list[Path]) -> list:
    """Freeze applicable on-disk ancestor instructions within declared roots."""
    files = set()
    for repo in repositories:
        for root in roots:
            if repo.is_relative_to(root):
                # Repo-root instructions come from the pinned Git tree, not
                # the mutable worktree. Only workspace ancestors are copied.
                current = repo.parent
                while current.is_relative_to(root):
                    candidate = current / "AGENTS.md"
                    if candidate.is_file():
                        files.add(candidate)
                    if current == root:
                        break
                    current = current.parent
    result = []
    for number, source in enumerate(sorted(files), 1):
        require(not source.is_symlink() and not getattr(source, "is_junction", lambda: False)(), "審查指示不能是連結。")
        content = source.read_bytes()
        relative = f"instructions/workspace-{number:03}.md"
        target = owned_path(context, relative)
        target.parent.mkdir(exist_ok=True)
        target.write_bytes(content)
        result.append({"origin": str(source), "file": relative, "sha256": hashlib.sha256(content).hexdigest()})
    return result


def validate_workspace_instructions(context: Path, records: list):
    for item in records:
        require(hashlib.sha256(owned_path(context, item["file"]).read_bytes()).hexdigest() == item["sha256"],
                "工作區審查指示摘要不符。")
