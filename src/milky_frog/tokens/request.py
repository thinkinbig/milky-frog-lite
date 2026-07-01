from __future__ import annotations

import json

from milky_frog.domain import Message, ModelRequest
from milky_frog.tokens.base import TokenCounter


def message_count_dict(message: Message) -> dict[str, str]:
    """Render a message for counting, folding tool-call args into the content.

    Assistant ``tool_calls`` carry their arguments outside ``content``; without
    folding them in they would be invisible to the token count.
    """
    content = message.content
    if message.tool_calls:
        content += json.dumps(
            [{"name": c.name, "arguments": c.arguments} for c in message.tool_calls]
        )
    return {"role": message.role.value, "content": content}


def count_request_tokens(counter: TokenCounter, request: ModelRequest) -> int:
    """Estimate the tokens a ``ModelRequest`` sends: messages plus tool schemas."""
    message_dicts = [message_count_dict(m) for m in request.messages]
    return counter.count_messages(message_dicts) + counter.count_tool_schemas(request.tools)
