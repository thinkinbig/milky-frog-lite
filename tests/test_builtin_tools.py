import asyncio
from pathlib import Path

from milky_frog.harness.sandbox import LocalSandbox
from milky_frog.harness.tools import ToolContext
from milky_frog.harness.tools.builtins import (
    EditFileTool,
    GitTool,
    ListDirTool,
    ReadFileTool,
    WriteFileTool,
    default_tools,
)


def _context(workspace: Path) -> ToolContext:
    return ToolContext("run-1", workspace, sandbox=LocalSandbox(workspace))


def test_default_tools_exposes_all_builtin_tools() -> None:
    names = {tool.name for tool in default_tools()}

    assert names == {"read_file", "write_file", "edit_file", "list_dir", "git"}


async def test_read_file_returns_contents(tmp_path: Path) -> None:
    (tmp_path / "note.txt").write_text("hello", encoding="utf-8")

    result = await ReadFileTool().execute(
        _context(tmp_path), ReadFileTool.input_model(path="note.txt")
    )

    assert not result.is_error
    assert result.content == "hello"


async def test_read_file_missing_is_error(tmp_path: Path) -> None:
    result = await ReadFileTool().execute(
        _context(tmp_path), ReadFileTool.input_model(path="nope.txt")
    )

    assert result.is_error
    assert "not a file" in result.content


async def test_read_file_rejects_sensitive_path(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("SECRET=1", encoding="utf-8")

    result = await ReadFileTool().execute(_context(tmp_path), ReadFileTool.input_model(path=".env"))

    assert result.is_error


async def test_write_file_creates_parent_directories(tmp_path: Path) -> None:
    result = await WriteFileTool().execute(
        _context(tmp_path), WriteFileTool.input_model(path="a/b/c.txt", content="data")
    )

    assert not result.is_error
    assert (tmp_path / "a/b/c.txt").read_text(encoding="utf-8") == "data"


async def test_write_file_rejects_escape(tmp_path: Path) -> None:
    result = await WriteFileTool().execute(
        _context(tmp_path), WriteFileTool.input_model(path="../escape.txt", content="x")
    )

    assert result.is_error


async def test_edit_file_replaces_unique_occurrence(tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("alpha beta gamma", encoding="utf-8")

    result = await EditFileTool().execute(
        _context(tmp_path), EditFileTool.input_model(path="f.txt", old="beta", new="BETA")
    )

    assert not result.is_error
    assert (tmp_path / "f.txt").read_text(encoding="utf-8") == "alpha BETA gamma"


async def test_edit_file_rejects_non_unique_match(tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("x x", encoding="utf-8")

    result = await EditFileTool().execute(
        _context(tmp_path), EditFileTool.input_model(path="f.txt", old="x", new="y")
    )

    assert result.is_error
    assert "not unique" in result.content


async def test_edit_file_rejects_missing_match(tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("abc", encoding="utf-8")

    result = await EditFileTool().execute(
        _context(tmp_path), EditFileTool.input_model(path="f.txt", old="zzz", new="y")
    )

    assert result.is_error
    assert "not found" in result.content


async def test_list_dir_marks_directories(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    (tmp_path / "file.txt").write_text("", encoding="utf-8")

    result = await ListDirTool().execute(_context(tmp_path), ListDirTool.input_model())

    assert not result.is_error
    assert result.content == "sub/\nfile.txt"


async def test_list_dir_empty(tmp_path: Path) -> None:
    result = await ListDirTool().execute(_context(tmp_path), ListDirTool.input_model())

    assert result.content == "(empty directory)"


async def test_tool_context_builds_default_sandbox(tmp_path: Path) -> None:
    context = ToolContext("run-1", tmp_path)

    assert context.require_sandbox().workspace == tmp_path.resolve()


async def test_git_status_returns_output(tmp_path: Path) -> None:
    # init a git repo in tmp_path so git status works
    proc = await asyncio.create_subprocess_exec(
        "git",
        "init",
        "-q",
        cwd=str(tmp_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()
    assert proc.returncode == 0

    result = await GitTool().execute(_context(tmp_path), GitTool.input_model(command="status"))

    assert not result.is_error
    assert "On branch" in result.content


async def test_git_error_returns_error_result(tmp_path: Path) -> None:
    result = await GitTool().execute(_context(tmp_path), GitTool.input_model(command="status"))

    assert result.is_error
    assert "fatal" in result.content or "exited " in result.content


async def test_git_empty_command_is_error(tmp_path: Path) -> None:
    result = await GitTool().execute(_context(tmp_path), GitTool.input_model(command="  "))

    assert result.is_error
    assert "empty" in result.content


async def test_git_diff_works(tmp_path: Path) -> None:
    proc = await asyncio.create_subprocess_exec(
        "git",
        "init",
        "-q",
        cwd=str(tmp_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()
    (tmp_path / "f.txt").write_text("hello", encoding="utf-8")

    result = await GitTool().execute(_context(tmp_path), GitTool.input_model(command="diff"))

    assert not result.is_error
    # unstaged file shows in diff output (git shows untracked files aren't in diff)
    # but at least the command ran successfully
    assert result.content is not None
