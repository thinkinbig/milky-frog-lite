import asyncio
from pathlib import Path

from milky_frog.harness.sandbox import LocalSandbox
from milky_frog.harness.tools import ToolContext
from milky_frog.harness.tools.builtins import (
    BashTool,
    EditFileTool,
    ListDirTool,
    ReadFileTool,
    WriteFileTool,
    default_tools,
)


def _context(workspace: Path) -> ToolContext:
    return ToolContext("run-1", workspace, sandbox=LocalSandbox(workspace))


def test_default_tools_exposes_all_builtin_tools() -> None:
    names = {tool.name for tool in default_tools()}

    assert names == {"read_file", "write_file", "edit_file", "list_dir", "bash"}


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


async def test_bash_empty_command_is_error(tmp_path: Path) -> None:
    result = await BashTool().execute(_context(tmp_path), BashTool.input_model(command="  "))

    assert result.is_error
    assert "empty" in result.content


async def test_bash_simple_echo(tmp_path: Path) -> None:
    result = await BashTool().execute(
        _context(tmp_path), BashTool.input_model(command="echo hello")
    )

    assert not result.is_error
    assert result.content == "hello"


async def test_bash_exit_code_error(tmp_path: Path) -> None:
    result = await BashTool().execute(_context(tmp_path), BashTool.input_model(command="exit 42"))

    assert result.is_error
    assert "exit code 42" in result.content


async def test_bash_grep_finds_matches(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text(
        "def hello():\n    pass\ndef world():\n    pass\n", encoding="utf-8"
    )
    (tmp_path / "b.py").write_text("class Foo:\n    pass\n", encoding="utf-8")

    result = await BashTool().execute(
        _context(tmp_path), BashTool.input_model(command="grep -rn 'def' .")
    )

    assert not result.is_error
    assert "a.py" in result.content
    assert "def hello" in result.content


async def test_bash_grep_no_matches(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")

    result = await BashTool().execute(
        _context(tmp_path), BashTool.input_model(command="grep -rn 'zzz_no_match' .")
    )

    assert result.is_error  # grep exits 1 when no matches
    assert "exit code 1" in result.content


async def test_bash_grep_subdirectory(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    (tmp_path / "root_file.py").write_text("def root(): pass\n", encoding="utf-8")
    (tmp_path / "sub" / "child.py").write_text("def child(): pass\n", encoding="utf-8")

    result = await BashTool().execute(
        _context(tmp_path), BashTool.input_model(command="grep -rn 'def' sub")
    )

    assert not result.is_error
    assert "child.py" in result.content
    assert "root_file.py" not in result.content


async def test_bash_os_error(tmp_path: Path, monkeypatch) -> None:
    async def failing_exec(*args, **kwargs):
        raise OSError("command not found")

    monkeypatch.setattr(asyncio, "create_subprocess_shell", failing_exec)

    result = await BashTool().execute(_context(tmp_path), BashTool.input_model(command="echo hi"))

    assert result.is_error
    assert "failed to run command" in result.content


async def test_bash_cwd_is_workspace(tmp_path: Path) -> None:
    (tmp_path / "note.txt").write_text("workspace file", encoding="utf-8")

    result = await BashTool().execute(
        _context(tmp_path), BashTool.input_model(command="cat note.txt")
    )

    assert not result.is_error
    assert "workspace file" in result.content


async def test_bash_stderr_without_stdout(tmp_path: Path) -> None:
    result = await BashTool().execute(
        _context(tmp_path), BashTool.input_model(command="echo 'err msg' >&2 && false")
    )

    assert result.is_error
    assert "exit code 1" in result.content
    assert "err msg" in result.content


async def test_bash_stderr_with_stdout(tmp_path: Path) -> None:
    result = await BashTool().execute(
        _context(tmp_path), BashTool.input_model(command="echo out && echo 'err msg' >&2 && false")
    )

    assert result.is_error
    assert "exit code 1" in result.content
    assert "out" in result.content
    assert "err msg" in result.content


async def test_bash_no_output(tmp_path: Path) -> None:
    result = await BashTool().execute(_context(tmp_path), BashTool.input_model(command="true"))

    assert not result.is_error
    assert result.content == "(no output)"


async def test_bash_timeout_returns_error(tmp_path: Path, monkeypatch) -> None:
    class FakeProcess:
        def communicate(self): ...
        def kill(self): ...
        async def wait(self): ...

    async def fake_shell(*args, **kwargs):
        return FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_shell", fake_shell)

    async def raise_timeout(*args, **kwargs):
        raise TimeoutError()

    monkeypatch.setattr(asyncio, "wait_for", raise_timeout)

    result = await BashTool().execute(_context(tmp_path), BashTool.input_model(command="sleep 100"))

    assert result.is_error
    assert "timed out" in result.content


async def test_bash_truncated_output(tmp_path: Path) -> None:
    result = await BashTool().execute(
        _context(tmp_path), BashTool.input_model(command="python3 -c 'print(\"x\" * 200000)'")
    )

    assert not result.is_error
    assert "Truncated" in result.content


# ── EditFileTool edge cases ──────────────────────────────────────────────


async def test_edit_identical_old_new_is_error(tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("hello", encoding="utf-8")

    result = await EditFileTool().execute(
        _context(tmp_path),
        EditFileTool.input_model(path="f.txt", old="hello", new="hello"),
    )

    assert result.is_error
    assert "identical" in result.content


async def test_edit_not_a_file_is_error(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()

    result = await EditFileTool().execute(
        _context(tmp_path), EditFileTool.input_model(path="sub", old="x", new="y")
    )

    assert result.is_error
    assert "not a file" in result.content


async def test_edit_missing_file_is_error(tmp_path: Path) -> None:
    result = await EditFileTool().execute(
        _context(tmp_path), EditFileTool.input_model(path="nope.txt", old="x", new="y")
    )

    assert result.is_error


async def test_edit_os_error_read_returns_error(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "f.txt"
    target.write_text("hello", encoding="utf-8")

    def failing_read_text(self, **kwargs):
        raise OSError("permission denied")

    monkeypatch.setattr(type(target), "read_text", failing_read_text)

    result = await EditFileTool().execute(
        _context(tmp_path),
        EditFileTool.input_model(path="f.txt", old="hello", new="bye"),
    )

    assert result.is_error
    assert "OSError" in result.content or "error" in result.content


async def test_edit_sandbox_violation_returns_error(tmp_path: Path) -> None:
    result = await EditFileTool().execute(
        _context(tmp_path), EditFileTool.input_model(path="../escape.txt", old="x", new="y")
    )

    assert result.is_error


async def test_edit_write_error_returns_error(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "f.txt").write_text("hello world", encoding="utf-8")

    def failing_write_text(self, content, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(type(tmp_path / "f.txt"), "write_text", failing_write_text)

    result = await EditFileTool().execute(
        _context(tmp_path),
        EditFileTool.input_model(path="f.txt", old="hello", new="bye"),
    )

    assert result.is_error
    assert "OSError" in result.content
