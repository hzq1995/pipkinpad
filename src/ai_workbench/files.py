"""Safe file operations rooted at one Workspace."""

from __future__ import annotations

import shutil
from pathlib import Path

from .security import Workspace


class FileService:
    def __init__(self, workspace: Workspace):
        self.workspace = workspace

    def list_tree(self, relative: str = "") -> list[dict]:
        directory = self.workspace.resolve(relative)
        if not directory.is_dir():
            raise ValueError("Not a directory")
        result = []
        for item in sorted(directory.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            if self.workspace.is_hidden(item):
                continue
            result.append({"path": self.workspace.relative(item), "name": item.name, "directory": item.is_dir()})
        return result

    def read_text(self, relative: str) -> str:
        path = self.workspace.resolve(relative)
        if not path.is_file():
            raise ValueError("Not a file")
        if path.stat().st_size > 1_000_000:
            raise ValueError("File is too large to edit")
        try:
            return path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            raise ValueError("不支持预览该文件（非文本文件或编码不是 UTF-8）")

    def write_text(self, relative: str, content: str) -> None:
        path = self.workspace.resolve(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def mkdir(self, relative: str) -> None:
        self.workspace.resolve(relative).mkdir(parents=True, exist_ok=False)

    def move(self, source: str, destination: str) -> None:
        src, dest = self.workspace.resolve(source), self.workspace.resolve(destination)
        dest.parent.mkdir(parents=True, exist_ok=True)
        src.rename(dest)

    def delete(self, relative: str) -> None:
        path = self.workspace.resolve(relative)
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
