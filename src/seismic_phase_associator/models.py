"""Result contract and JSON-safety helpers."""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PipelineResult:
    project: str
    data: dict[str, Any]

    def __post_init__(self) -> None:
        if not isinstance(self.project, str) or not self.project:
            raise ValueError("project must be a non-empty string")
        if not isinstance(self.data, dict):
            raise ValueError("data must be an object")


def json_safe(value: Any) -> Any:
    """Deep-copy a value so the report remains valid, stable JSON.

    Non-finite floats cannot be rendered as standard JSON and never compare
    equal to themselves, so they are preserved as their canonical string form
    (``"NaN"``, ``"Infinity"``, ``"-Infinity"``).  Everything else, including
    unknown keys on a raw pick, is copied verbatim.
    """

    if isinstance(value, float) and not math.isfinite(value):
        return repr(value)
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    return copy.deepcopy(value)
