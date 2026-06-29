from pathlib import Path

from milky_frog.adapters.local import LocalSandbox
from milky_frog.harness.tools import ToolContext
from milky_frog.harness.tools.builtins import ReadFileTool
from milky_frog.harness.tools.spill import spill_full_output
from milky_frog.harness.tools.truncate import truncate_tool_output
from milky_frog.project import ProjectConfig


def test_truncate_without_workspace_keeps_old_behavior() -> None:
    out = truncate_tool_output("x" * 1000, max_chars=100)

    assert "Truncated" in out
    assert "saved to" not in out


def test_truncate_splits_on_line_boundaries_not_mid_line() -> None:
    lines = [f"row-{index:03d}-" + ("x" * 20) + "\n" for index in range(50)]
    full = "".join(lines)

    out = truncate_tool_output(full, max_chars=500)

    assert "Truncated" in out
    head, _, tail = out.partition("\n\n... (Truncated")
    assert head.endswith("\n")
    assert not head.endswith("x\nrow")
    assert tail.endswith("\n") or tail.split("\n", 1)[-1].endswith("\n")


def test_truncate_first_omitted_line_matches_head_line_count(tmp_path: Path) -> None:
    full = "".join(f"line {index}\n" for index in range(100))

    out = truncate_tool_output(full, max_chars=200, workspace=tmp_path, label="read")

    head_part = out.split("\n\n... (Truncated")[0]
    head_lines = head_part.count("\n")
    offset = int(out.split("offset=")[1].split(".")[0])
    assert offset == head_lines + 1


def test_truncate_with_workspace_spills_full_text(tmp_path: Path) -> None:
    full = "".join(f"line {i}\n" for i in range(500))

    out = truncate_tool_output(full, max_chars=100, workspace=tmp_path, label="grep")

    assert "saved to .milky-frog/tool-output/" in out
    assert "offset=" in out  # actionable line to start paging the omitted middle
    spilled = list((tmp_path / ".milky-frog" / "tool-output").glob("*grep*.txt"))
    assert len(spilled) == 1
    assert spilled[0].read_text(encoding="utf-8") == full
    # Spilled output (may carry secrets) is kept out of version control.
    assert (tmp_path / ".milky-frog" / "tool-output" / ".gitignore").read_text() == "*\n"


def test_spill_returns_workspace_relative_path(tmp_path: Path) -> None:
    rel = spill_full_output(tmp_path, "bash", "hello")

    assert rel is not None
    assert rel.startswith(".milky-frog/tool-output/")
    assert (tmp_path / rel).read_text(encoding="utf-8") == "hello"


async def test_read_file_spills_and_is_retrievable(tmp_path: Path) -> None:
    # A read past the truncation limit spills the full text; the model can then
    # read the spill file back — closing the retrieval loop.
    sandbox = LocalSandbox(tmp_path, config=ProjectConfig(read_output_max_chars=1000))
    context = ToolContext("run-1", tmp_path, sandbox=sandbox)
    big = "".join(f"row {i}\n" for i in range(1000))
    (tmp_path / "big.txt").write_text(big, encoding="utf-8")

    result = await ReadFileTool().execute(context, ReadFileTool.input_model(path="big.txt"))

    assert "saved to .milky-frog/tool-output/" in result.content
    assert "row 500" not in result.content  # the middle was truncated away
    rel = result.content.split("saved to ")[1].split(";")[0].strip()

    # The recovery path is a windowed read of the spill file (offset/limit), not
    # a whole re-read — that is how the omitted middle comes back.
    retrieved = await ReadFileTool().execute(
        context, ReadFileTool.input_model(path=rel, offset=501, limit=1)
    )
    assert not retrieved.is_error
    assert retrieved.content == "[lines 501-501 of 1000]\nrow 500\n"
