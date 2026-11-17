from __future__ import annotations

from typing import Any

from .engine import DEFAULT_WINDOW, calculate
from .parser import parse_payload
from .report import build_result

PROJECT = "seismic-phase-associator"


def run(payload: Any, *, window: float = DEFAULT_WINDOW) -> dict:
    """Run the full pipeline on a raw payload.

    This is the orchestration boundary: parsing, association and reporting are
    separate modules so each can be exercised independently.  Any invalid pick
    is carried all the way to the report with its untouched original payload.
    """

    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")

    records, rejected, corrections, diagnostics = parse_payload(payload)
    data = calculate(records, window=window, corrections=corrections)

    # Parser-level rejections are explainable in the same way as engine-level
    # ones: reason + detail + the raw pick that caused it.
    parse_rejections = [
        {
            "reason": item["reason"],
            "detail": item["detail"],
            "station": (
                item["raw_pick"].get("station")
                if isinstance(item["raw_pick"], dict)
                else None
            ),
            "phase": (
                item["raw_pick"].get("phase")
                if isinstance(item["raw_pick"], dict)
                else None
            ),
            "time": (
                item["raw_pick"].get("time")
                if isinstance(item["raw_pick"], dict)
                else None
            ),
            "source_index": item["source_index"],
            "raw_pick": item["raw_pick"],
        }
        for item in rejected
    ]

    all_rejected = parse_rejections + data["rejected_picks"]
    all_rejected.sort(
        key=lambda item: (
            item["reason"],
            _sortable(item.get("station")),
            _sortable(item.get("phase")),
            _sortable(item.get("time")),
            _sortable(item.get("source_index")),
            repr(item.get("raw_pick")),
        )
    )

    data["rejected_picks"] = all_rejected
    data["rejected_pick_count"] = len(all_rejected)
    data["summary"]["rejected_pick_count"] = len(all_rejected)
    data["summary"]["parse_rejection_count"] = len(parse_rejections)
    data["diagnostics"] = sorted(
        diagnostics,
        key=lambda item: (
            item.get("kind", ""),
            _sortable(item.get("station")),
            repr(item.get("raw_value")),
        ),
    )
    data["diagnostics_count"] = len(data["diagnostics"])

    return build_result(PROJECT, data)


def _sortable(value: Any) -> tuple:
    """Total ordering key that tolerates mixed and missing values."""

    if value is None:
        return (0, "")
    if isinstance(value, bool):
        return (1, str(value))
    if isinstance(value, (int, float)):
        return (2, repr(float(value)))
    return (3, str(value))
