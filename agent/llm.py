import anthropic

from agent.config import MODEL, api_key


def call(
    system_prompt: str,
    tools: list[dict],
    messages: list[dict],
) -> anthropic.types.Message:
    client = anthropic.Anthropic(api_key=api_key())
    return client.messages.create(
        model=MODEL,
        max_tokens=4096,
        system=system_prompt,
        tools=tools,
        messages=messages,
    )
