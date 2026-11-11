from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

_FILTER_CONFIG = re.compile(r"^filter\.(.+)\.(clean|smudge|process|required)$", re.IGNORECASE)


def git_environment() -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("GIT_")
    }
    environment["GIT_CONFIG_NOSYSTEM"] = "1"
    environment["GIT_CONFIG_GLOBAL"] = os.devnull
    return environment


def git_command(repository: Path, arguments: list[str]) -> list[str]:
    env = git_environment()
    filters = subprocess.run(
        [
            "git", "-C", str(repository), "config", "--local", "--name-only",
            "--get-regexp", r"^filter\..*\.(clean|smudge|process|required)$",
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    filter_names = {
        match.group(1)
        for line in filters.stdout.splitlines()
        if (match := _FILTER_CONFIG.match(line))
    }
    config = [
        setting
        for filter_name in sorted(filter_names)
        for setting in (
            f"filter.{filter_name}.clean=",
            f"filter.{filter_name}.smudge=",
            f"filter.{filter_name}.process=",
            f"filter.{filter_name}.required=false",
        )
    ]
    return [
        "git",
        *[part for setting in config for part in ("-c", setting)],
        "-c", "core.fsmonitor=false",
        "-c", "core.hooksPath=/dev/null",
        "-c", "core.attributesFile=/dev/null",
        "-c", "core.pager=cat",
        "-C", str(repository),
        *arguments,
    ]


def run_git(
    repository: Path,
    arguments: list[str],
    *,
    capture_output: bool = True,
    text: bool = True,
    check: bool = False,
    timeout: int | None = None,
) -> subprocess.CompletedProcess[str] | subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        git_command(repository, arguments),
        capture_output=capture_output,
        text=text,
        check=check,
        timeout=timeout,
        env=git_environment(),
    )
