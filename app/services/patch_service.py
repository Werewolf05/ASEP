from __future__ import annotations

import hashlib
import os
import stat
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any, ClassVar

from app.agent.base import AIProvider
from app.models.issue import IssueCreate
from app.models.task import EngineeringTask, PatchArtifact, WorktreeRecord
from app.repository.git_evidence import changed_file_records, complete_git_diff
from app.services.worktree_manager import WorktreeManager


class PatchGenerationService:
    """Validate a complete proposal, then apply it only inside its managed worktree."""

    forbidden_names: ClassVar[set[str]] = {
        ".git", ".env", ".ssh", ".aws", ".azure", ".docker", ".kube",
        ".gnupg", ".config", "id_rsa", "id_dsa",
        "authorized_keys", "secrets.yml", "credentials", "credentials.json",
    }
    allowed_suffixes: ClassVar[set[str]] = {
        ".py", ".txt", ".md", ".json", ".yaml", ".yml", ".toml", ".ini",
        ".cfg", ".js", ".ts", ".tsx", ".jsx", ".html", ".css", ".sh",
    }
    max_file_size_bytes: ClassVar[int] = 256 * 1024
    max_patch_size_bytes: ClassVar[int] = 1 * 1024 * 1024

    def __init__(
        self,
        provider: AIProvider | None = None,
        worktree_manager: WorktreeManager | None = None,
    ) -> None:
        self.provider = provider
        self.worktree_manager = worktree_manager or WorktreeManager()

    def generate_patch(self, issue: IssueCreate, task: EngineeringTask) -> PatchArtifact:
        if self.provider is None:
            raise ValueError("Patch generation requires an AI provider implementation.")
        if task.worktree_path is None:
            raise ValueError(f"Task '{task.task_id}' does not have a valid worktree.")
        worktree = WorktreeRecord(
            task_id=task.task_id,
            original_repository_path=task.repository_path,
            worktree_path=task.worktree_path,
            base_commit_sha=task.base_commit_sha or "",
        )
        worktree_root = self.worktree_manager.validate_worktree(worktree)
        proposal = self.provider.generate_patch_proposal(issue, task)
        if proposal.get("status") == "rejected":
            raise ValueError(f"Patch proposal rejected: {proposal.get('reason', 'Unknown reason')}")

        changes = proposal.get("changes")
        if not isinstance(changes, list) or not changes:
            raise ValueError("Patch proposal does not contain any valid file changes.")
        validated = self._validate_proposal(changes, worktree_root)
        proposal_size = sum(
            len(change["content"].encode("utf-8"))
            for change in validated
            if isinstance(change.get("content"), str)
        )
        if proposal_size > self.max_patch_size_bytes:
            raise ValueError("Patch exceeds the configured total patch-size limit.")

        snapshots: dict[str, tuple[bytes | None, int | None]] = {}
        created_directories: set[str] = set()
        for change in validated:
            for name in (change["path"], change.get("new_path")):
                if isinstance(name, str) and name not in snapshots:
                    snapshots[name] = (
                        self._safe_read(worktree_root, name),
                        self._safe_mode(worktree_root, name),
                    )

        try:
            for change in validated:
                self._apply_change(worktree_root, change, created_directories)
            diff_bytes = complete_git_diff(worktree_root)
            if len(diff_bytes) > self.max_patch_size_bytes:
                raise ValueError("Complete patch exceeds the configured total patch-size limit.")
            file_changes = changed_file_records(
                worktree_root,
                [
                    (change["path"], change["new_path"])
                    for change in validated
                    if change["action"] == "rename"
                ],
            )
            changed_files = sorted(
                {
                    path
                    for change in file_changes
                    for path in (change.path, change.previous_path)
                    if path is not None
                }
            )
            file_hashes: dict[str, str] = {}
            for path in changed_files:
                content = self._safe_read(worktree_root, path)
                if content is not None:
                    file_hashes[path] = hashlib.sha256(content).hexdigest()
        except (OSError, ValueError, RuntimeError, TypeError, subprocess.SubprocessError):
            self._restore_snapshots(worktree_root, snapshots)
            self._remove_created_directories(worktree_root, created_directories)
            raise

        return PatchArtifact(
            task_id=task.task_id,
            patch_id=f"patch-{task.task_id}",
            patch_sha256=hashlib.sha256(diff_bytes).hexdigest(),
            patch_size=len(diff_bytes),
            diff=diff_bytes.decode("utf-8"),
            changed_files=changed_files,
            file_changes=file_changes,
            changed_file_hashes=file_hashes,
            summary=str(proposal.get("summary", "Patch generated successfully.")),
        )

    def _validate_proposal(
        self,
        changes: list[Any],
        worktree_root: Path,
    ) -> list[dict[str, str]]:
        validated: list[dict[str, str]] = []
        touched: set[str] = set()
        for item in changes:
            if not isinstance(item, dict):
                raise TypeError("Each change entry must be a dictionary.")
            action = str(item.get("action", "modify")).lower()
            if action not in {"create", "modify", "replace", "delete", "rename"}:
                raise ValueError(f"Unsupported patch action '{action}'.")
            path = self._validate_target_path(str(item.get("path", "")), worktree_root)
            if path in touched:
                raise ValueError(f"Patch proposal modifies '{path}' more than once.")
            touched.add(path)
            change = {"action": action, "path": path}
            if action == "rename":
                new_path = self._validate_target_path(str(item.get("new_path", "")), worktree_root)
                if new_path in touched:
                    raise ValueError(f"Patch proposal modifies '{new_path}' more than once.")
                touched.add(new_path)
                change["new_path"] = new_path
                if self._safe_read(worktree_root, path) is None:
                    raise ValueError(f"Rename source '{path}' does not exist.")
                rename_source = self._safe_read(worktree_root, path)
                if rename_source is not None:
                    self._validate_text_file(path, rename_source)
                if self._safe_read(worktree_root, new_path) is not None:
                    raise ValueError(f"Rename destination '{new_path}' already exists.")
                validated.append(change)
                continue

            existing = self._safe_read(worktree_root, path)
            if existing is not None:
                self._validate_text_file(path, existing)
            if action == "create" and existing is not None:
                raise ValueError(f"Create target '{path}' already exists.")
            if action in {"modify", "delete"} and existing is None:
                raise ValueError(f"Patch target '{path}' does not exist.")
            if action == "delete":
                validated.append(change)
                continue

            content = item.get("content")
            if not isinstance(content, str):
                raise TypeError(f"Patch content for '{path}' must be a string.")
            content_bytes = content.encode("utf-8")
            if b"\x00" in content_bytes:
                raise ValueError(f"Binary patch content is not supported: '{path}'.")
            if len(content_bytes) > self.max_file_size_bytes:
                raise ValueError(f"Patch content for '{path}' exceeds the file-size limit.")
            change["content"] = content
            validated.append(change)

        return validated

    def _remove_created_directories(self, root: Path, directories: set[str]) -> None:
        for path in sorted(directories, key=lambda item: len(PurePosixPath(item).parts), reverse=True):
            parent_fd, name = self._open_parent(root, path, create=False)
            try:
                os.rmdir(name, dir_fd=parent_fd)
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise RuntimeError(f"Patch rollback could not remove temporary directory '{path}'.") from exc
            finally:
                os.close(parent_fd)

    def _validate_target_path(self, requested: str, worktree_root: Path) -> str:
        if (
            not requested
            or requested.startswith("/")
            or "\\" in requested
            or (len(requested) >= 2 and requested[1] == ":")
        ):
            raise ValueError(f"Absolute or invalid patch path rejected: '{requested}'.")
        candidate = PurePosixPath(requested)
        if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
            raise ValueError(f"Unsafe patch path rejected: '{requested}'.")
        if any(self._is_sensitive_name(part) for part in candidate.parts):
            raise ValueError(f"Sensitive path rejected: '{requested}'.")
        if candidate.suffix.lower() not in self.allowed_suffixes:
            raise ValueError(f"Unsupported file type for patch target '{requested}'.")

        root = worktree_root.resolve(strict=True)
        current = root
        for part in candidate.parts:
            current = current / part
            try:
                metadata = current.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(metadata.st_mode):
                raise ValueError(f"Symlink paths are rejected: '{requested}'.")
            if stat.S_ISREG(metadata.st_mode) and metadata.st_nlink != 1:
                raise ValueError(f"Hard-linked patch targets are rejected: '{requested}'.")
            if current != root and current.exists() and not current.is_dir() and part != candidate.parts[-1]:
                raise ValueError(f"Non-directory path component in '{requested}'.")
        try:
            current.resolve(strict=False).relative_to(root)
        except ValueError as exc:
            raise ValueError(f"Patch path escapes the worktree: '{requested}'.") from exc
        return candidate.as_posix()

    @classmethod
    def _is_sensitive_name(cls, name: str) -> bool:
        lowered = name.casefold()
        return (
            lowered in cls.forbidden_names
            or lowered.startswith(".env.")
            or lowered.endswith((".pem", ".key", ".p12", ".pfx"))
            or "secret" in lowered
            or "credential" in lowered
            or "password" in lowered
            or lowered in {".npmrc", ".pypirc", ".netrc", "known_hosts"}
        )

    @staticmethod
    def _validate_text_file(path: str, content: bytes) -> None:
        if b"\x00" in content:
            raise ValueError(f"Binary patch targets are not supported: '{path}'.")
        try:
            content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"Binary patch targets are not supported: '{path}'.") from exc

    def _safe_read(self, root: Path, relative_path: str) -> bytes | None:
        try:
            parent_fd, name = self._open_parent(root, relative_path, create=False)
        except FileNotFoundError:
            return None
        try:
            try:
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd)
            except FileNotFoundError:
                return None
            with os.fdopen(fd, "rb") as stream:
                data = stream.read(self.max_file_size_bytes + 1)
            if len(data) > self.max_file_size_bytes:
                raise ValueError(f"Existing patch target '{relative_path}' exceeds the file-size limit.")
            return data
        finally:
            os.close(parent_fd)

    def _open_parent(
        self,
        root: Path,
        relative_path: str,
        create: bool,
        created_directories: set[str] | None = None,
    ) -> tuple[int, str]:
        parts = PurePosixPath(relative_path).parts
        descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            for index, part in enumerate(parts[:-1]):
                try:
                    next_descriptor = os.open(
                        part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor
                    )
                except FileNotFoundError:
                    if not create:
                        raise
                    os.mkdir(part, mode=0o755, dir_fd=descriptor)
                    if created_directories is not None:
                        created_directories.add("/".join(parts[: index + 1]))
                    next_descriptor = os.open(
                        part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor
                    )
                os.close(descriptor)
                descriptor = next_descriptor
            return descriptor, parts[-1]
        except OSError:
            os.close(descriptor)
            raise

    def _safe_mode(self, root: Path, relative_path: str) -> int | None:
        try:
            parent_fd, name = self._open_parent(root, relative_path, create=False)
        except FileNotFoundError:
            return None
        try:
            try:
                metadata = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                return None
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError(f"Patch target '{relative_path}' is not a regular file.")
            return stat.S_IMODE(metadata.st_mode)
        finally:
            os.close(parent_fd)

    def _write_file(
        self,
        root: Path,
        relative_path: str,
        content: bytes,
        *,
        exclusive: bool,
        mode: int | None = None,
        created_directories: set[str] | None = None,
    ) -> None:
        parent_fd, name = self._open_parent(
            root,
            relative_path,
            create=True,
            created_directories=created_directories,
        )
        temporary_name = f".asep-{os.urandom(12).hex()}.tmp"
        try:
            if mode is None:
                try:
                    old_metadata = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                except FileNotFoundError:
                    mode = 0o644
                else:
                    if not stat.S_ISREG(old_metadata.st_mode):
                        raise ValueError(f"Patch target '{relative_path}' is not a regular file.")
                    mode = stat.S_IMODE(old_metadata.st_mode)
            fd = os.open(
                temporary_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                mode,
                dir_fd=parent_fd,
            )
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            if exclusive:
                os.link(
                    temporary_name,
                    name,
                    src_dir_fd=parent_fd,
                    dst_dir_fd=parent_fd,
                    follow_symlinks=False,
                )
                os.unlink(temporary_name, dir_fd=parent_fd)
            else:
                os.replace(temporary_name, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        finally:
            try:
                os.unlink(temporary_name, dir_fd=parent_fd)
            except FileNotFoundError:
                pass
            os.close(parent_fd)

    def _unlink_file(self, root: Path, relative_path: str) -> None:
        parent_fd, name = self._open_parent(root, relative_path, create=False)
        try:
            os.unlink(name, dir_fd=parent_fd)
        finally:
            os.close(parent_fd)

    def _apply_change(
        self,
        root: Path,
        change: dict[str, str],
        created_directories: set[str],
    ) -> None:
        action = change["action"]
        path = change["path"]
        if action == "delete":
            self._unlink_file(root, path)
        elif action == "rename":
            content = self._safe_read(root, path)
            if content is None:
                raise FileNotFoundError(f"Rename source '{path}' disappeared.")
            mode = self._safe_mode(root, path)
            self._write_file(
                root,
                change["new_path"],
                content,
                exclusive=True,
                mode=mode,
                created_directories=created_directories,
            )
            self._unlink_file(root, path)
        else:
            self._write_file(
                root,
                path,
                change["content"].encode("utf-8"),
                exclusive=action == "create",
                created_directories=created_directories,
            )

    def _restore_snapshots(
        self, root: Path, snapshots: dict[str, tuple[bytes | None, int | None]]
    ) -> None:
        for path, (content, mode) in reversed(list(snapshots.items())):
            try:
                if content is None:
                    self._unlink_file(root, path)
                else:
                    self._write_file(root, path, content, exclusive=False, mode=mode)
            except FileNotFoundError:
                continue
