"""Local session and workspace-boundary helpers."""

from __future__ import annotations

import secrets
from pathlib import Path


class WorkspaceSecurityError(ValueError):
    """Raised when a path would escape the authorized workspace."""


class Workspace:
    def __init__(self, root: Path):
        self.root = root.resolve(strict=True)

    def resolve(self, relative_path: str = "") -> Path:
        candidate = (self.root / relative_path).resolve(strict=False)
        try:
            candidate.relative_to(self.root)
        except ValueError as error:
            raise WorkspaceSecurityError("Path is outside the workspace") from error
        return candidate

    def relative(self, path: Path) -> str:
        resolved = path.resolve(strict=False)
        try:
            return resolved.relative_to(self.root).as_posix()
        except ValueError as error:
            raise WorkspaceSecurityError("Path is outside the workspace") from error

    def is_hidden(self, path: Path) -> bool:
        return any(part.startswith(".") for part in path.relative_to(self.root).parts)


def new_session_token() -> str:
    return secrets.token_urlsafe(32)
