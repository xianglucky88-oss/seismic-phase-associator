"""Stable output envelope.

Reporting never re-derives association decisions.  Its job is to place the
engine's data into a versioned, deterministic envelope, to normalise ordering
one final time, and to guarantee the audit trail survives serialisation.  Every
rejected or anomalous pick in the envelope carries the raw pick that caused the
decision, so a reviewer can reproduce a difference without guessing.
"""

from __future__ import annotations

import json
from typing import Any, Iterable

from .models import PipelineResult, json_safe

SCHEMA_VERSION = 1


def _time_key(value: Any) -> tuple:
    """Total order over possibly-missing times (numbers sort numerically)."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return (1, str(value))
    return (0, float(value))


def _event_key(event: dict) -> tuple:
    return (
        _time_key(event.get("origin_time")),
        tuple(str(s) for s in (event.get("stations") or ())),
        tuple(
            (
                str(p.get("station")),
                str(p.get("phase")),
                _time_key(p.get("corrected_time")),
            )
            for p in event.get("picks", ())
        ),
    )


def _pick_key(pick: dict) -> tuple:
    return (
        _time_key(pick.get("corrected_time")),
        str(pick.get("station")),
        str(pick.get("phase")),
        str(pick.get("source_index")),
        repr(pick.get("raw_pick")),
    )


def _rejection_key(item: dict) -> tuple:
    return (
        str(item.get("reason")),
        str(item.get("station")),
        str(item.get("phase")),
        repr(item.get("time")),
        str(item.get("source_index")),
        repr(item.get("raw_pick")),
    )


def _normalise_events(events: Iterable[dict]) -> list[dict]:
    normalised: list[dict] = []
    for event in sorted(events, key=_event_key):
        item = json_safe(event)
        item["picks"] = sorted(item.get("picks", []), key=_pick_key)
        item["stations"] = sorted(set(item.get("stations") or ()))
        item["source_stations"] = sorted(set(item.get("source_stations") or ()))
        item["phases"] = sorted(set(item.get("phases") or ()))
        item["pick_count"] = len(item["picks"])
        normalised.append(item)
    return normalised


def build_result(project: str, data: dict) -> dict:
    """Wrap engine data in the stable report envelope."""

    if not isinstance(data, dict):
        raise ValueError("data must be an object")

    envelope_data = json_safe(data)
    envelope_data["events"] = _normalise_events(envelope_data.get("events", []))
    envelope_data["rejected_picks"] = sorted(
        envelope_data.get("rejected_picks", []), key=_rejection_key
    )
    envelope_data["rejected_pick_count"] = len(envelope_data["rejected_picks"])

    summary = dict(envelope_data.get("summary", {}))
    summary["rejected_pick_count"] = envelope_data["rejected_pick_count"]
    summary["accepted_pick_count"] = sum(
        event["pick_count"] for event in envelope_data["events"]
    )
    summary["accepted_event_count"] = len(envelope_data["events"])
    envelope_data["summary"] = summary

    result = PipelineResult(project=project, data=envelope_data)
    return {
        "project": result.project,
        "status": "ok",
        "schema_version": SCHEMA_VERSION,
        **result.data,
    }


def stable_json(value: Any) -> str:
    """Canonical JSON rendering used for golden comparisons and diffs."""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)
