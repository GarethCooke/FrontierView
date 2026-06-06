import os

from dotenv import load_dotenv

load_dotenv()

MODEL = "claude-haiku-4-5-20251001"
MAX_ITERS = 8
MAX_TOKENS = 4096

if MAX_ITERS < 1:
    raise ValueError(f"MAX_ITERS must be at least 1, got {MAX_ITERS}")


def api_key() -> str:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. "
            "Export it before running the agent: export ANTHROPIC_API_KEY=sk-ant-..."
        )
    return key
