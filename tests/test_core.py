from pathlib import Path

import pytest

from ai_workbench.ai import AIClient
from ai_workbench.config import AISettings
from ai_workbench.files import FileService
from ai_workbench.security import Workspace, WorkspaceSecurityError


def test_workspace_rejects_escape(tmp_path: Path):
    workspace = Workspace(tmp_path)
    with pytest.raises(WorkspaceSecurityError):
        workspace.resolve("../outside")


def test_file_crud_and_hidden_filter(tmp_path: Path):
    service = FileService(Workspace(tmp_path))
    service.write_text("nested/note.txt", "hello")
    (tmp_path / ".secret").write_text("no")
    assert service.read_text("nested/note.txt") == "hello"
    assert service.list_tree() == [{"path": "nested", "name": "nested", "directory": True}]
    service.move("nested/note.txt", "moved.txt")
    assert service.read_text("moved.txt") == "hello"
    service.delete("moved.txt")
    assert not (tmp_path / "moved.txt").exists()


def test_file_tree_ignores_symlink_outside_workspace(tmp_path: Path):
    outside = tmp_path.parent / "outside"
    outside.mkdir(exist_ok=True)
    try:
        (tmp_path / "outside-link").symlink_to(outside, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"Creating symbolic links is unavailable: {error}")

    assert FileService(Workspace(tmp_path)).list_tree() == []


def test_command_blocks_are_extracted():
    # Parsing is deliberately transparent: model suggestions remain text until the user confirms.
    from ai_workbench.ai import COMMAND_BLOCK
    assert [command.strip() for command in COMMAND_BLOCK.findall("Try:\n```bash\necho safe\n```")] == ["echo safe"]


def test_ai_bash_tool_is_independent_from_browser_terminal():
    from ai_workbench.ai import BASH_TOOL

    function = BASH_TOOL["function"]
    assert function["name"] == "run_bash_command"
    assert function["parameters"]["required"] == ["command"]
    assert "session_id" not in function["parameters"]["properties"]


def test_public_settings_redacts_key():
    public = AISettings(api_key="super-secret").public()
    assert public["api_key_configured"] is True
    assert "api_key" not in public
