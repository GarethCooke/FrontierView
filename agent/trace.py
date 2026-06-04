import json


def step(label: str, content) -> None:
    if isinstance(content, (dict, list)):
        content = json.dumps(content, indent=2)
    print(f"\n[{label}]\n{content}", flush=True)
