from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class Settings(BaseModel):
    """Application-level configuration for the platform foundation."""

    model_config = ConfigDict(extra="ignore")

    app_name: str = Field(default="asep")
    debug: bool = Field(default=False)
    allowed_repository_roots: list[str] = Field(
        default_factory=lambda: ["/tmp", "/home"],
    )
    issue_max_length: int = Field(default=12000)


def load_settings() -> Settings:
    raw_roots = os.getenv("ASEP_ALLOWED_REPO_ROOTS", "/tmp,/home")
    parsed_roots = [root.strip() for root in raw_roots.split(",") if root.strip()]
    return Settings(
        app_name=os.getenv("ASEP_APP_NAME", "asep"),
        debug=os.getenv("ASEP_DEBUG", "false").lower() == "true",
        allowed_repository_roots=parsed_roots if parsed_roots else ["/tmp", "/home"],
        issue_max_length=int(os.getenv("ASEP_ISSUE_MAX_LENGTH", "12000")),
    )


def normalize_repo_roots(paths: Sequence[str] | None = None) -> list[Path]:
    source = list(paths) if paths is not None else load_settings().allowed_repository_roots
    normalized: list[Path] = []
    for item in source:
        candidate = Path(item).expanduser().resolve(strict=False)
        if candidate not in normalized:
            normalized.append(candidate)
    return normalized
