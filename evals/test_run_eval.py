"""Deterministic checks for truncation eval scoring."""

from __future__ import annotations

from evals.run_eval import format_expected, matches_final_message
from evals.tool_collector import ToolCallRecord, summarize_tool_call

_AGENT_ANSWER = """The file **was changed** — 50,003 insertions and 1 deletion.

The value of `"final"` in the diff is:

```
"y"
```
"""


def test_matches_raw_diff_line() -> None:
    assert matches_final_message('+  "final": "y"', '"final": "y"')


def test_matches_formatted_value_answer() -> None:
    assert matches_final_message(_AGENT_ANSWER, ['"final": "y"', '"y"'])


def test_matches_all_requires_every_pattern() -> None:
    message = "CODE_A=ALPHA-91 and CODE_B=BRAVO-42"
    patterns = ["ALPHA-91", "BRAVO-42"]
    assert matches_final_message(message, patterns, match="all")
    assert not matches_final_message("only ALPHA-91", patterns, match="all")


def test_format_expected_joins_alternatives() -> None:
    assert format_expected(["a", "b"]) == "a | b"


def test_summarize_bash_command() -> None:
    record = ToolCallRecord("bash", {"command": "git diff --stat"}, False)
    assert summarize_tool_call(record) == "bash: git diff --stat"
