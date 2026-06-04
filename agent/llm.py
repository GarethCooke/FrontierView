import anthropic

from agent.config import MODEL, api_key

_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=api_key())
    return _client


def call(
    system_prompt: str,
    tools: list[dict],
    messages: list[dict],
) -> anthropic.types.Message:
    cached_tools = (
        [*tools[:-1], {**tools[-1], "cache_control": {"type": "ephemeral"}}]
        if tools
        else tools
    )
    return _get_client().messages.create(
        model=MODEL,
        max_tokens=4096,
        system=[{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}],
        tools=cached_tools,
        messages=messages,
    )
