import json

from agent import llm, tools, trace
from agent.config import MAX_ITERS

_SYSTEM_PROMPT = """You are a quantitative analyst assistant for FrontierView, \
a pre-trade market impact analysis tool based on the Almgren-Chriss (2005) model.

Answer questions about optimal trade execution by calling the available tools:
- cost_and_variance: compute expected cost and variance for a given execution schedule
- optimal_schedule: compute the Almgren-Chriss optimal schedule at a given risk-aversion level λ

Available symbols: AAPL, MSFT, GOOGL, JPM, SPY.

Always use tool calls to retrieve numeric results — do not estimate or calculate \
market impact values yourself. When you have enough information, give a clear, \
concise answer in plain English with the key numbers."""


def run(question: str) -> str:
    messages: list[dict] = [{"role": "user", "content": question}]
    response = None

    for _ in range(MAX_ITERS):
        response = llm.call(_SYSTEM_PROMPT, tools.TOOLS, messages)

        text_parts = [b.text for b in response.content if b.type == "text"]
        if text_parts:
            trace.step("REASONING", "\n".join(text_parts))

        if response.stop_reason == "max_tokens":
            partial = " ".join(text_parts)
            trace.step("TRUNCATED", "Response cut off by max_tokens limit")
            return f"[TRUNCATED] {partial}"

        if response.stop_reason != "tool_use":
            answer = "\n".join(text_parts)
            if not answer:
                answer = f"[No text produced; stop_reason={response.stop_reason!r}]"
            trace.step("FINAL ANSWER", answer)
            return answer

        messages.append({"role": "assistant", "content": response.content})

        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                trace.step("TOOL CALL", {"name": block.name, "args": block.input})
                result = tools.dispatch(block.name, block.input)
                trace.step("TOOL RESULT", result)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result),
                })

        messages.append({"role": "user", "content": tool_results})

    last_text = "\n".join(b.text for b in response.content if b.type == "text")
    return (
        f"Stopped after {MAX_ITERS} iterations without a final answer.\n\n"
        f"Last response: {last_text}"
    )
