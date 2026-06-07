import os

from dotenv import load_dotenv

load_dotenv()

MODEL = "claude-haiku-4-5-20251001"
MAX_ITERS = 8
MAX_TOKENS = 4096
COMPACTION_THRESHOLD_TOKENS = 6000  # approximate; actual trigger ~25-30% higher due to JSON overhead
COMPACTION_KEEP_RECENT_TURNS = 4    # (assistant+user) turn pairs to keep verbatim
LLM_MAX_RETRIES = 3                 # retry budget for provider 429/5xx
TOOL_RETRY_BUDGET = 2               # max model self-corrections per tool-call site

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
