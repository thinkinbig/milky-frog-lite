from __future__ import annotations

import json
from pathlib import Path

from milky_frog.domain import Message, MessageRole, ModelRequest
from milky_frog.harness.prompt import BuildSystemPromptOptions, build_system_prompt, system_prompt
from milky_frog.harness.prompt_context import AgentContext, ContextFile
from milky_frog.harness.tokens import ApproxCharCounter, TokenBudget
from milky_frog.harness.tools import ToolRegistry, default_tools


def _message_dict(role: str, content: str) -> dict[str, str]:
    return {"role": role, "content": content}


def test_count_text_uses_char_ratio_with_minimum_one() -> None:
    counter = ApproxCharCounter()

    assert counter.count_text("") == 0
    assert counter.count_text("a") == 1
    assert counter.count_text("abcd") == 1
    assert counter.count_text("abcdefgh") == 2


def test_count_messages_adds_per_message_overhead() -> None:
    counter = ApproxCharCounter()
    messages = (_message_dict("user", "abcd"),)

    expected = 4 + counter.count_text("user") + counter.count_text("abcd")
    assert counter.count_messages(messages) == expected


def test_count_tool_schemas_sums_serialized_schema_text() -> None:
    counter = ApproxCharCounter()
    schemas = ({"name": "read_file", "description": "Read a file"},)

    assert counter.count_tool_schemas(schemas) == counter.count_text(json.dumps(schemas[0]))


def test_system_prompt_estimate_matches_message_counter(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    counter = ApproxCharCounter()
    prompt = system_prompt(workspace)

    estimate = counter.count_messages((_message_dict("system", prompt),))

    assert estimate > 200
    assert estimate == 4 + counter.count_text("system") + counter.count_text(prompt)


def test_built_system_prompt_grows_with_injected_context(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    counter = ApproxCharCounter()

    minimal = build_system_prompt(
        BuildSystemPromptOptions(workspace=workspace, agent_context=AgentContext())
    )
    expanded = build_system_prompt(
        BuildSystemPromptOptions(
            workspace=workspace,
            agent_context=AgentContext(
                append_system="Always run pytest before committing.",
                context_files=(ContextFile(workspace / "AGENTS.md", "Never skip the formatter."),),
                skill_locations=(
                    ("review", "Review code carefully", workspace / "skills" / "review"),
                ),
            ),
        )
    )

    minimal_tokens = counter.count_messages((_message_dict("system", minimal),))
    expanded_tokens = counter.count_messages((_message_dict("system", expanded),))

    assert expanded_tokens > minimal_tokens


def test_token_budget_count_for_typical_run_request(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    counter = ApproxCharCounter()
    budget = TokenBudget()
    budget._counter = counter

    system = system_prompt(workspace)
    user = "Fix the failing test in tests/test_example.py"
    tools = ToolRegistry(default_tools()).schemas()
    request = ModelRequest(
        (
            Message(MessageRole.SYSTEM, system),
            Message(MessageRole.USER, user),
        ),
        tools,
    )

    message_dicts = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    expected = counter.count_messages(message_dicts) + counter.count_tool_schemas(tools)

    assert budget._count_request_tokens(request) == expected
    assert expected > counter.count_messages(message_dicts)
