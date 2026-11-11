from __future__ import annotations

import os
import re
import stat
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import IO

from app.models.task import WorktreeRecord
from app.repository.git_utils import git_command, git_environment, run_git

TASK_ID_PATTERN = re.compile(r"task-[0-9a-f]{32}\Z")


class WorktreeManager:
    """Creates and removes task-owned Git worktrees without running checkout hooks."""

    def __init__(self, worktree_root: str | None = None) -> None:
        base = (
            Path(worktree_root)
            if worktree_root
            else Path.home() / ".local" / "share" / "asep" / "worktrees"
        ).expanduser()
        if base.is_symlink():
            raise ValueError("Worktree root cannot be a symbolic link.")
        base.mkdir(parents=True, mode=0o700, exist_ok=True)
        metadata = base.lstat()
        if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid():
            raise ValueError("Worktree root must be a directory owned by the ASEP user.")
        if stat.S_IMODE(metadata.st_mode) & 0o077:
            os.chmod(base, 0o700)
        self.worktree_root = base.resolve(strict=True)

    @staticmethod
    def validate_task_id(task_id: str) -> None:
        if not TASK_ID_PATTERN.fullmatch(task_id):
            raise ValueError("Invalid task ID.")

    def create_worktree(self, repo_path: str, task_id: str | None = None) -> WorktreeRecord:
        requested_repo = Path(repo_path).expanduser().resolve(strict=True)
        if not requested_repo.is_dir():
            raise ValueError("Repository path must be a Git working directory.")

        git_check = self._git(requested_repo, ["rev-parse", "--is-inside-work-tree"])
        if git_check != "true":
            raise ValueError("The provided path is not inside a Git working tree.")

        common_dir_text = self._git(
            requested_repo, ["rev-parse", "--path-format=absolute", "--git-common-dir"]
        )
        common_dir = Path(common_dir_text).resolve(strict=True)
        main_repository = common_dir.parent if common_dir.name == ".git" else requested_repo
        main_repository = main_repository.resolve(strict=True)

        dirty_status = self._git(
            requested_repo, ["status", "--porcelain=v1", "--untracked-files=all"]
        )
        if requested_repo != main_repository:
            dirty_status = "\n".join(
                item
                for item in (
                    dirty_status,
                    self._git(
                        main_repository,
                        ["status", "--porcelain=v1", "--untracked-files=all"],
                    ),
                )
                if item
            )
        if dirty_status:
            raise ValueError(
                "The original repository has uncommitted changes. Commit or stash them before "
                "creating a task worktree; ASEP will not copy or alter those changes."
            )

        base_commit = self._git(requested_repo, ["rev-parse", "HEAD"])
        if (
            self.worktree_root == main_repository
            or self.worktree_root in main_repository.parents
            or main_repository in self.worktree_root.parents
        ):
            raise ValueError("Worktree root must be separate from the original repository.")
        resolved_task_id = task_id or f"task-{uuid.uuid4().hex}"
        self.validate_task_id(resolved_task_id)

        target_dir = self.worktree_root / resolved_task_id
        if (
            target_dir.parent != self.worktree_root
            or target_dir.exists()
            or target_dir.is_symlink()
        ):
            raise ValueError("Task worktree target is invalid or already exists.")

        result = subprocess.run(
            git_command(
                main_repository,
                [
                    "worktree", "add", "--detach", "--no-checkout",
                    str(target_dir), base_commit,
                ],
            ),
            capture_output=True,
            text=True,
            check=False,
            env=git_environment(),
        )
        if result.returncode != 0:
            if target_dir.exists():
                self._remove_partial_worktree(main_repository, target_dir)
            raise ValueError(
                "Failed to register task worktree: "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )

        try:
            self._git(target_dir, ["read-tree", base_commit])
            self._populate_worktree(main_repository, target_dir, base_commit)
            actual_worktree = Path(
                self._git(target_dir, ["rev-parse", "--show-toplevel"])
            ).resolve(strict=True)
            if actual_worktree != target_dir:
                raise ValueError("Git created a worktree at an unexpected path.")
        except (OSError, RuntimeError, TypeError, ValueError, subprocess.SubprocessError):
            self._remove_partial_worktree(main_repository, target_dir)
            raise

        return WorktreeRecord(
            task_id=resolved_task_id,
            original_repository_path=str(main_repository),
            worktree_path=str(target_dir),
            base_commit_sha=base_commit,
            dirty_repository=False,
            created_at=datetime.now(UTC),
        )

    def validate_worktree(self, record: WorktreeRecord) -> Path:
        self.validate_task_id(record.task_id)
        expected_entry = self.worktree_root / record.task_id
        if expected_entry.is_symlink():
            raise ValueError("Managed worktree path cannot be a symbolic link.")
        expected = expected_entry.resolve(strict=True)
        recorded_path = Path(record.worktree_path)
        if (
            recorded_path != expected_entry
            or recorded_path.is_symlink()
            or expected.parent != self.worktree_root
            or not expected.is_dir()
        ):
            raise ValueError("Worktree record does not identify this task's managed worktree.")
        original_repository = Path(record.original_repository_path).resolve(strict=True)
        if original_repository == expected:
            raise ValueError("Original repository cannot be used as a task worktree.")
        original_common = Path(
            self._git(
                original_repository,
                ["rev-parse", "--path-format=absolute", "--git-common-dir"],
            )
        ).resolve(strict=True)
        worktree_common = Path(
            self._git(expected, ["rev-parse", "--path-format=absolute", "--git-common-dir"])
        ).resolve(strict=True)
        if original_common != worktree_common:
            raise ValueError("Worktree is not linked to the recorded original repository.")
        return expected

    def cleanup_worktree(self, record: WorktreeRecord) -> bool:
        """Remove only the clean, registered worktree described by its stored record."""
        try:
            worktree_path = self.validate_worktree(record)
        except (FileNotFoundError, ValueError):
            return False

        original_repository = Path(record.original_repository_path).resolve(strict=True)
        listed = self._git(original_repository, ["worktree", "list", "--porcelain"])
        registered_paths = {
            line.removeprefix("worktree ")
            for line in listed.splitlines()
            if line.startswith("worktree ")
        }
        if str(worktree_path) not in registered_paths:
            return False

        result = subprocess.run(
            git_command(original_repository, ["worktree", "remove", str(worktree_path)]),
            capture_output=True,
            text=True,
            check=False,
            env=git_environment(),
        )
        if result.returncode != 0:
            return False
        return not worktree_path.exists()

    def _populate_worktree(self, repository: Path, worktree: Path, commit: str) -> None:
        listing = run_git(
            repository,
            ["ls-tree", "-r", "-z", "--full-tree", commit],
            text=False,
            timeout=30,
        )
        if listing.returncode != 0:
            raise ValueError("Unable to list repository tree for the task worktree.")
        if not isinstance(listing.stdout, bytes):
            raise TypeError("Git tree output was not returned as bytes.")
        entries = [item for item in listing.stdout.split(b"\0") if item]
        cat_file = subprocess.Popen(
            git_command(repository, ["cat-file", "--batch"]),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=git_environment(),
        )
        try:
            if cat_file.stdin is None or cat_file.stdout is None:
                raise RuntimeError("Unable to open Git object stream.")
            symlinks: list[Path] = []
            for entry in entries:
                metadata, separator, raw_path = entry.partition(b"\t")
                if not separator:
                    raise ValueError("Git returned an invalid tree entry.")
                mode, object_type, object_id = metadata.split()
                relative = os.fsdecode(raw_path)
                relative_path = self._validate_tree_path(relative)
                if mode == b"160000" and object_type == b"commit":
                    self._make_directory(worktree, relative_path)
                    continue
                if object_type != b"blob" or mode not in {b"100644", b"100755", b"120000"}:
                    raise ValueError(f"Unsupported Git tree entry at '{relative}'.")

                cat_file.stdin.write(object_id + b"\n")
                cat_file.stdin.flush()
                header = cat_file.stdout.readline().split()
                if len(header) != 3 or header[1] != b"blob":
                    raise ValueError(f"Unable to read Git blob for '{relative}'.")
                remaining = int(header[2])
                if mode == b"120000":
                    if remaining > 4096:
                        raise ValueError(f"Repository symlink '{relative}' exceeds the target limit.")
                    link_target = cat_file.stdout.read(remaining)
                    self._read_blob_terminator(cat_file.stdout)
                    link_text = os.fsdecode(link_target)
                    self._create_safe_symlink(worktree, relative_path, link_text)
                    symlinks.append(worktree / relative_path)
                    continue

                parent_fd, name = self._open_output_parent(worktree, relative_path)
                try:
                    descriptor = os.open(
                        name,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                        0o755 if mode == b"100755" else 0o644,
                        dir_fd=parent_fd,
                    )
                    with os.fdopen(descriptor, "wb") as target:
                        while remaining:
                            chunk = cat_file.stdout.read(min(remaining, 64 * 1024))
                            if not chunk:
                                raise ValueError(f"Git blob '{relative}' was truncated.")
                            target.write(chunk)
                            remaining -= len(chunk)
                    self._read_blob_terminator(cat_file.stdout)
                finally:
                    os.close(parent_fd)

            cat_file.stdin.close()
            return_code = cat_file.wait(timeout=60)
            error = cat_file.stderr.read() if cat_file.stderr else b""
            if return_code != 0:
                raise ValueError(f"Git object extraction failed: {error.decode(errors='replace')}")
            for symlink in symlinks:
                try:
                    symlink.resolve(strict=False).relative_to(worktree)
                except (RuntimeError, ValueError) as exc:
                    raise ValueError("Repository contains a symlink escaping its worktree.") from exc
        finally:
            if cat_file.poll() is None:
                cat_file.kill()
                cat_file.wait()
            if cat_file.stdin is not None and not cat_file.stdin.closed:
                cat_file.stdin.close()
            if cat_file.stdout is not None:
                cat_file.stdout.close()
            if cat_file.stderr is not None:
                cat_file.stderr.close()

    @staticmethod
    def _validate_tree_path(path: str) -> PurePosixPath:
        candidate = PurePosixPath(path)
        if (
            not path
            or candidate.is_absolute()
            or any(part in {"", ".", "..", ".git"} for part in candidate.parts)
        ):
            raise ValueError("Repository tree contains an unsafe path.")
        return candidate

    @staticmethod
    def _open_output_parent(root: Path, relative: PurePosixPath) -> tuple[int, str]:
        descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            for part in relative.parts[:-1]:
                try:
                    next_descriptor = os.open(
                        part,
                        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                        dir_fd=descriptor,
                    )
                except FileNotFoundError:
                    os.mkdir(part, mode=0o755, dir_fd=descriptor)
                    next_descriptor = os.open(
                        part,
                        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                        dir_fd=descriptor,
                    )
                os.close(descriptor)
                descriptor = next_descriptor
            return descriptor, relative.parts[-1]
        except OSError:
            os.close(descriptor)
            raise

    @classmethod
    def _make_directory(cls, root: Path, relative: PurePosixPath) -> None:
        parent_fd, name = cls._open_output_parent(root, relative)
        try:
            try:
                os.mkdir(name, mode=0o755, dir_fd=parent_fd)
            except FileExistsError:
                existing = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                if not stat.S_ISDIR(existing.st_mode):
                    raise ValueError("Git submodule path conflicts with a non-directory.")
        finally:
            os.close(parent_fd)

    @classmethod
    def _create_safe_symlink(
        cls, root: Path, relative: PurePosixPath, link_target: str
    ) -> None:
        if not link_target or Path(link_target).is_absolute():
            raise ValueError("Repository contains an absolute or empty symlink target.")
        parent_fd, name = cls._open_output_parent(root, relative)
        try:
            os.symlink(link_target, name, dir_fd=parent_fd)
        finally:
            os.close(parent_fd)

    @staticmethod
    def _read_blob_terminator(stream: IO[bytes]) -> None:
        terminator = stream.read(1)
        if terminator != b"\n":
            raise ValueError("Git object stream had an invalid blob terminator.")

    def _remove_partial_worktree(self, repository: Path, target: Path) -> None:
        if target.parent != self.worktree_root or not target.exists():
            return
        listed = self._git(repository, ["worktree", "list", "--porcelain"])
        if str(target) in {
            line.removeprefix("worktree ")
            for line in listed.splitlines()
            if line.startswith("worktree ")
        }:
            result = subprocess.run(
                git_command(repository, ["worktree", "remove", "--force", str(target)]),
                capture_output=True,
                text=True,
                check=False,
                env=git_environment(),
            )
            if result.returncode != 0:
                raise RuntimeError("Unable to safely remove the incomplete task worktree.")
            return
        try:
            target.rmdir()
        except OSError as exc:
            raise RuntimeError(
                "An incomplete unregistered worktree remains at its managed task path."
            ) from exc

    @staticmethod
    def _git(repository: Path, arguments: list[str]) -> str:
        result = run_git(repository, arguments)
        if result.returncode != 0:
            stderr = result.stderr if isinstance(result.stderr, str) else result.stderr.decode(errors="replace")
            stdout = result.stdout if isinstance(result.stdout, str) else result.stdout.decode(errors="replace")
            raise ValueError(stderr.strip() or stdout.strip() or "Git command failed.")
        output = result.stdout
        if not isinstance(output, str):
            return output.decode(errors="replace").strip()
        return output.strip()
