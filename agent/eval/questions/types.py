from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Question:
    """A single eval question with ground truth and expected behaviour."""

    id: str
    text: str
    expected_tool_path: list[str]   # minimal expected tool calls (any order)
    gt_values: dict[str, float]     # ground-truth floats, keyed by tool summary field names
    out_of_tool: bool = False       # True → expect decline/caveat, not a numeric answer
    synthetic: bool = False         # True → expect a synthetic-data caveat in the answer
    tolerance_overrides: dict = field(default_factory=dict)  # {key: {"rtol": …, "atol": …}}
    source: str = "generated"       # "generated" or "curated"
    notes: str = ""
