import json
import os

_ENABLED = os.environ.get("AGENT_TRACE", "0") != "0"


def step(label: str, content) -> None:
    if not _ENABLED:
        return
    if isinstance(content, (dict, list)):
        content = json.dumps(content, indent=2)
    print(f"\n[{label}]\n{content}", flush=True)
