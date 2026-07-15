import subprocess
from pathlib import Path

from stubs import FixedOutcomeSandbox

from milky_frog.adapters.local import LocalSandbox
from milky_frog.core.sandbox import CommandStartError
from milky_frog.harness.tools import ToolContext
from milky_frog.harness.tools.builtins import (
    BashTool,
    EditFileTool,
    GrepTool,
    ListDirTool,
    ReadFileTool,
    WriteFileTool,
    default_tools,
)
from milky_frog.project import ProjectConfig


def _context(
    workspace: Path, config: ProjectConfig | None = None, search_prefix: str = ""
) -> ToolContext:
    return ToolContext(
        "run-1",
        workspace,
        sandbox=LocalSandbox(workspace, config=config),
        search_prefix=search_prefix,
    )


def test_tool_context_make_output_path_with_prefix(tmp_path: Path) -> None:
    ctx = _context(tmp_path, search_prefix="src")
    assert ctx.make_output_path("module.py") == "src/module.py"
    assert ctx.make_output_path("subdir/file.py") == "src/subdir/file.py"


def test_tool_context_make_output_path_without_prefix(tmp_path: Path) -> None:
    ctx = _context(tmp_path, search_prefix="")
    assert ctx.make_output_path("module.py") == "module.py"

    ctx_dot = _context(tmp_path, search_prefix=".")
    assert ctx_dot.make_output_path("module.py") == "module.py"


def test_default_tools_exposes_all_builtin_tools() -> None:
    names = {tool.name for tool in default_tools()}

    assert names == {
        "read_file",
        "write_file",
        "edit_file",
        "list_dir",
        "grep",
        "bash",
        "fetch",
        "merge_worktree",
    }


def test_default_tools_omits_web_search_without_jina_key() -> None:
    names = {tool.name for tool in default_tools(jina_api_key=None)}

    assert "web_search" not in names


def test_default_tools_includes_web_search_with_jina_key() -> None:
    names = {tool.name for tool in default_tools(jina_api_key="a-key")}

    assert "web_search" in names


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


async def test_read_file_offset_and_limit_returns_window(tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("one\ntwo\nthree\nfour\nfive\n", encoding="utf-8")

    result = await ReadFileTool().execute(
        _context(tmp_path), ReadFileTool.input_model(path="f.txt", offset=2, limit=2)
    )

    assert not result.is_error
    assert result.content == "[lines 2-3 of 5]\ntwo\nthree\n"


async def test_read_file_offset_to_end(tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("a\nb\nc\n", encoding="utf-8")

    result = await ReadFileTool().execute(
        _context(tmp_path), ReadFileTool.input_model(path="f.txt", offset=2)
    )

    assert not result.is_error
    assert result.content == "[lines 2-3 of 3]\nb\nc\n"


async def test_read_file_full_window_omits_header(tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("a\nb\n", encoding="utf-8")

    result = await ReadFileTool().execute(
        _context(tmp_path), ReadFileTool.input_model(path="f.txt", limit=10)
    )

    assert not result.is_error
    assert result.content == "a\nb\n"


async def test_read_file_offset_past_end_is_error(tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("a\nb\n", encoding="utf-8")

    result = await ReadFileTool().execute(
        _context(tmp_path), ReadFileTool.input_model(path="f.txt", offset=5)
    )

    assert result.is_error
    assert "past the end" in result.content


async def test_read_file_empty_file_with_offset_is_error_not_reversed_header(
    tmp_path: Path,
) -> None:
    (tmp_path / "empty.txt").write_text("", encoding="utf-8")

    result = await ReadFileTool().execute(
        _context(tmp_path), ReadFileTool.input_model(path="empty.txt", offset=5)
    )

    assert result.is_error
    assert "past the end" in result.content
    assert "lines 5-0" not in result.content


async def test_read_file_on_directory_returns_listing(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text("", encoding="utf-8")
    (tmp_path / "pkg" / "sub").mkdir()

    result = await ReadFileTool().execute(_context(tmp_path), ReadFileTool.input_model(path="pkg"))

    assert not result.is_error
    assert "is a directory" in result.content
    assert "sub/" in result.content
    assert "a.py" in result.content


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


# ── GrepTool ─────────────────────────────────────────────────────────────


async def test_grep_matches_with_workspace_relative_paths(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("class Tool:\n    pass\n", encoding="utf-8")
    (tmp_path / "src" / "b.py").write_text("x = 1\n", encoding="utf-8")

    result = await GrepTool().execute(
        _context(tmp_path), GrepTool.input_model(pattern="class Tool")
    )

    assert not result.is_error
    assert result.content == "src/a.py:1:class Tool:"


async def test_grep_no_matches(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")

    result = await GrepTool().execute(_context(tmp_path), GrepTool.input_model(pattern="nope"))

    assert result.content == "(no matches)"


async def test_grep_invalid_regex_is_error(tmp_path: Path) -> None:
    result = await GrepTool().execute(_context(tmp_path), GrepTool.input_model(pattern="("))

    assert result.is_error
    assert "invalid regex" in result.content


async def test_grep_never_surfaces_denied_files(tmp_path: Path) -> None:
    # The load-bearing guarantee: a recursive grep must never read .env or .git,
    # so secrets can't leak through an approval-free tool.
    (tmp_path / ".env").write_text("SECRET_KEY=topsecret\n", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("SECRET_KEY=alsosecret\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("SECRET_KEY = load()\n", encoding="utf-8")

    result = await GrepTool().execute(
        _context(tmp_path), GrepTool.input_model(pattern="SECRET_KEY")
    )

    assert not result.is_error
    assert result.content == "app.py:1:SECRET_KEY = load()"
    assert "topsecret" not in result.content
    assert "alsosecret" not in result.content


async def test_grep_context_lines_show_surrounding(tmp_path: Path) -> None:
    (tmp_path / "f.py").write_text(
        "import os\ndef target():\n    return 1\nx = 2\n", encoding="utf-8"
    )

    result = await GrepTool().execute(
        _context(tmp_path), GrepTool.input_model(pattern="def target", context=1)
    )

    assert not result.is_error
    assert result.content == (
        "f.py-1-import os\n"  # context line uses '-'
        "f.py:2:def target():\n"  # match line uses ':'
        "f.py-3-    return 1"  # context line uses '-'
    )


async def test_grep_context_merges_overlapping_windows(tmp_path: Path) -> None:
    (tmp_path / "f.py").write_text("a\nhit\nb\nhit\nc\n", encoding="utf-8")

    result = await GrepTool().execute(
        _context(tmp_path), GrepTool.input_model(pattern="hit", context=1)
    )

    # Windows for lines 2 and 4 overlap at line 3 → one merged group, no '--'.
    assert "--" not in result.content
    assert result.content == ("f.py-1-a\nf.py:2:hit\nf.py-3-b\nf.py:4:hit\nf.py-5-c")


async def test_grep_spill_contains_full_results_not_collection_prefix(tmp_path: Path) -> None:
    # Collect-all-then-truncate must spill the complete search result, not an
    # early collection prefix that stopped once output grew past search_output_max_chars.
    sandbox = LocalSandbox(tmp_path, config=ProjectConfig(search_output_max_chars=1000))
    context = ToolContext("run-1", tmp_path, sandbox=sandbox)
    for index in range(50):
        (tmp_path / f"f{index:02d}.txt").write_text(f"needle line {index:02d}\n", encoding="utf-8")

    result = await GrepTool().execute(context, GrepTool.input_model(pattern="needle"))

    assert not result.is_error
    assert "saved to .milky-frog/tool-output/" in result.content
    spilled = list((tmp_path / ".milky-frog" / "tool-output").glob("*grep*.txt"))
    assert len(spilled) == 1
    spilled_text = spilled[0].read_text(encoding="utf-8")
    assert spilled_text.count("needle") == 50
    assert "f49.txt" in spilled_text


async def test_grep_spill_preserves_full_long_match_line(tmp_path: Path) -> None:
    long_tail = "z" * 1500
    (tmp_path / "min.js").write_text(f"needle{long_tail}\n", encoding="utf-8")
    sandbox = LocalSandbox(tmp_path, config=ProjectConfig(search_output_max_chars=1000))
    context = ToolContext("run-1", tmp_path, sandbox=sandbox)

    result = await GrepTool().execute(context, GrepTool.input_model(pattern="needle"))

    assert not result.is_error
    assert "saved to .milky-frog/tool-output/" in result.content
    spilled = list((tmp_path / ".milky-frog" / "tool-output").glob("*grep*.txt"))
    assert len(spilled) == 1
    assert long_tail in spilled[0].read_text(encoding="utf-8")


async def test_grep_skips_spill_directory(tmp_path: Path) -> None:
    # A prior truncated tool result spilled here; grep must not surface it as a match.
    spill_dir = tmp_path / ".milky-frog" / "tool-output"
    spill_dir.mkdir(parents=True)
    (spill_dir / "20260101_grep_abcd1234.txt").write_text("needle in spill\n", encoding="utf-8")
    (tmp_path / "real.txt").write_text("needle in source\n", encoding="utf-8")

    result = await GrepTool().execute(_context(tmp_path), GrepTool.input_model(pattern="needle"))

    assert not result.is_error
    assert "real.txt" in result.content
    assert "tool-output" not in result.content


async def test_grep_rejects_escaping_path(tmp_path: Path) -> None:
    result = await GrepTool().execute(
        _context(tmp_path), GrepTool.input_model(pattern="x", path="../outside")
    )

    assert result.is_error


async def test_grep_searches_a_single_file(tmp_path: Path) -> None:
    (tmp_path / "only.py").write_text("hit here\nmiss\nhit again\n", encoding="utf-8")

    result = await GrepTool().execute(
        _context(tmp_path), GrepTool.input_model(pattern="hit", path="only.py")
    )

    assert result.content == "only.py:1:hit here\nonly.py:3:hit again"


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


async def test_bash_start_error(tmp_path: Path) -> None:
    context = ToolContext(
        "run-1",
        tmp_path,
        sandbox=FixedOutcomeSandbox(tmp_path, CommandStartError("command not found")),
    )

    result = await BashTool().execute(context, BashTool.input_model(command="echo hi"))

    assert result.is_error
    assert "failed to run command" in result.content
    assert "command not found" in result.content


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


async def test_bash_strips_ansi_from_model_content_but_keeps_display_content(
    tmp_path: Path,
) -> None:
    result = await BashTool().execute(
        _context(tmp_path),
        BashTool.input_model(command="printf '\\033[31mred\\033[0m\\n'"),
    )

    assert not result.is_error
    assert result.content == "red"
    assert result.display_content is not None
    assert "\x1b[31mred" in result.display_content


async def test_bash_large_stderr_does_not_deadlock(tmp_path: Path) -> None:
    result = await BashTool().execute(
        _context(tmp_path),
        BashTool.input_model(command="python3 -c 'import sys; sys.stderr.write(\"e\" * 200000)'"),
    )

    assert not result.is_error
    assert "Truncated" in result.content
    spilled = list((tmp_path / ".milky-frog" / "tool-output").glob("*bash*.txt"))
    assert len(spilled) == 1
    assert spilled[0].read_text(encoding="utf-8").count("e") == 200_000


async def test_bash_no_output(tmp_path: Path) -> None:
    result = await BashTool().execute(_context(tmp_path), BashTool.input_model(command="true"))

    assert not result.is_error
    assert result.content == "(no output)"


async def test_bash_timeout_returns_error(tmp_path: Path) -> None:
    result = await BashTool().execute(
        _context(tmp_path, ProjectConfig(bash_timeout_seconds=1)),
        BashTool.input_model(command="python3 -c 'import time; time.sleep(5)'"),
    )

    assert result.is_error
    assert "timed out" in result.content


async def test_bash_truncated_output(tmp_path: Path) -> None:
    result = await BashTool().execute(
        _context(tmp_path), BashTool.input_model(command="python3 -c 'print(\"x\" * 200000)'")
    )

    assert not result.is_error
    assert "Truncated" in result.content
    assert "saved to .milky-frog/tool-output/" in result.content
    spilled = list((tmp_path / ".milky-frog" / "tool-output").glob("*bash*.txt"))
    assert len(spilled) == 1
    assert spilled[0].read_text(encoding="utf-8").count("x") == 200_000


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    (path / "note.txt").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "note.txt"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=path,
        check=True,
        capture_output=True,
    )


async def test_bash_git_log_completes_without_pager(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)

    result = await BashTool().execute(
        _context(tmp_path), BashTool.input_model(command="git log --oneline -5")
    )

    assert not result.is_error
    assert "initial" in result.content


async def test_bash_git_show_completes_without_pager(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    result = await BashTool().execute(
        _context(tmp_path), BashTool.input_model(command=f"git show {commit}")
    )

    assert not result.is_error
    assert "initial" in result.content


async def test_bash_closed_stdin_avoids_read_hang(tmp_path: Path) -> None:
    result = await BashTool().execute(
        _context(tmp_path),
        BashTool.input_model(command="read -r line || echo stdin-closed"),
    )

    assert not result.is_error
    assert result.content == "stdin-closed"


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
