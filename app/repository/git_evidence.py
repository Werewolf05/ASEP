from __future__ import annotations

import stat
from pathlib import Path, PurePosixPath

from app.models.task import FileChange
from app.repository.git_utils import run_git


def untracked_paths(worktree: Path) -> list[str]:
    result = run_git(
        worktree,
        ["ls-files", "--others", "--exclude-standard", "-z"],
        text=False,
        timeout=15,
    )
    if result.returncode != 0:
        raise ValueError("Unable to inspect untracked task files.")
    if not isinstance(result.stdout, bytes):
        raise TypeError("Git path output was not returned as bytes.")
    return [
        path.decode(errors="surrogateescape")
        for path in result.stdout.split(b"\0")
        if path
    ]


def complete_git_diff(
    worktree: Path,
    command_log: list[list[str]] | None = None,
) -> bytes:
    tracked_arguments = [
        "diff", "--no-ext-diff", "--no-textconv", "--binary",
        "--find-renames", "HEAD", "--",
    ]
    if command_log is not None:
        command_log.append(["git", "-C", "<task-worktree>", *tracked_arguments])
    tracked = run_git(
        worktree,
        tracked_arguments,
        text=False,
        timeout=30,
    )
    if tracked.returncode != 0:
        raise ValueError("Unable to generate tracked-file patch diff.")
    if not isinstance(tracked.stdout, bytes):
        raise TypeError("Git diff output was not returned as bytes.")

    complete = bytearray(tracked.stdout)
    for path in untracked_paths(worktree):
        if not _safe_untracked_path(worktree, path):
            raise ValueError(f"Unsafe untracked path cannot be included in the patch: '{path}'.")
        untracked_arguments = [
            "diff", "--no-index", "--no-ext-diff", "--no-textconv",
            "--binary", "--", "/dev/null", path,
        ]
        if command_log is not None:
            command_log.append(["git", "-C", "<task-worktree>", *untracked_arguments])
        untracked = run_git(
            worktree,
            untracked_arguments,
            text=False,
            timeout=30,
        )
        if untracked.returncode not in {0, 1}:
            raise ValueError(f"Unable to include new file '{path}' in patch diff.")
        if not isinstance(untracked.stdout, bytes):
            raise TypeError("Git diff output was not returned as bytes.")
        complete.extend(untracked.stdout)
    return bytes(complete)


def _safe_untracked_path(worktree: Path, relative_path: str) -> bool:
    candidate = PurePosixPath(relative_path)
    if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        return False
    current = worktree
    for part in candidate.parts:
        current = current / part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            return False
        if stat.S_ISLNK(metadata.st_mode):
            return False
        if (
            stat.S_ISREG(metadata.st_mode)
            and (metadata.st_nlink != 1 or metadata.st_size > 256 * 1024)
        ):
            return False
    try:
        current.resolve(strict=True).relative_to(worktree.resolve(strict=True))
    except (OSError, RuntimeError, ValueError):
        return False
    return current.is_file()


def changed_file_records(
    worktree: Path,
    explicit_renames: list[tuple[str, str]] | None = None,
) -> list[FileChange]:
    result = run_git(
        worktree,
        [
            "diff", "--name-status", "-z", "--no-ext-diff", "--no-textconv",
            "--find-renames", "HEAD", "--",
        ],
        text=False,
        timeout=15,
    )
    if result.returncode != 0:
        raise ValueError("Unable to record changed file paths.")
    if not isinstance(result.stdout, bytes):
        raise TypeError("Git status output was not returned as bytes.")

    tokens = [token.decode(errors="surrogateescape") for token in result.stdout.split(b"\0") if token]
    changes: list[FileChange] = []
    index = 0
    while index < len(tokens):
        change_status = tokens[index]
        index += 1
        if change_status.startswith(("R", "C")):
            previous = tokens[index]
            path = tokens[index + 1]
            index += 2
            changes.append(
                FileChange(status=change_status, path=path, previous_path=previous)
            )
        else:
            changes.append(FileChange(status=change_status, path=tokens[index]))
            index += 1

    changes.extend(
        FileChange(status="A", path=path)
        for path in untracked_paths(worktree)
        if not any(change.path == path for change in changes)
    )
    for previous, path in explicit_renames or []:
        changes = [
            change
            for change in changes
            if not (
                (change.status == "D" and change.path == previous)
                or (change.status == "A" and change.path == path)
            )
        ]
        changes.append(
            FileChange(status="R100", path=path, previous_path=previous)
        )
    return changes
