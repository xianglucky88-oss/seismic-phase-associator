"""Input validation and record extraction.

The parser is the only place where raw user input is interpreted.  It keeps
the original object for every row so later stages (and the final report) can
always explain a decision by pointing at the exact pick that caused it.
"""

from __future__ import annotations

import math
from typing import Any, Mapping

from .models import json_safe


SUPPORTED_PHASES = frozenset({"P", "S"})

# Magnitudes outside this range are not physically meaningful for the local
# association problem.  They are reported as anomalies but never used to make
# an association decision.
MAGNITUDE_ABS_LIMIT = 10.0


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _rejection(index: int, reason: str, detail: str, raw_pick: Any) -> dict:
    return {
        "source_index": index,
        "reason": reason,
        "detail": detail,
        "raw_pick": json_safe(raw_pick),
    }


def normalise_pick(raw: Any, index: int) -> tuple[dict | None, dict | None]:
    """Normalise one raw pick.

    Returns ``(record, None)`` for an associable pick and ``(None, rejection)``
    for a pick that cannot even enter the association.  A rejection always
    carries a machine-readable reason and the untouched original pick.
    """

    if not isinstance(raw, dict):
        return None, _rejection(index, "invalid_record", "pick must be an object", raw)

    station = raw.get("station")
    if not isinstance(station, str) or not station.strip():
        return None, _rejection(
            index, "missing_station", "station must be a non-empty string", raw
        )

    phase = raw.get("phase")
    if not isinstance(phase, str) or not phase.strip():
        return None, _rejection(
            index, "missing_phase", "phase must be a non-empty string", raw
        )
    phase = phase.strip().upper()
    if phase not in SUPPORTED_PHASES:
        return None, _rejection(
            index,
            "unsupported_phase",
            "phase must be P or S, got %r" % (raw.get("phase"),),
            raw,
        )

    if "time" not in raw or raw.get("time") is None:
        return None, _rejection(index, "missing_time", "time is required", raw)
    time_value = raw.get("time")
    if not _is_number(time_value):
        return None, _rejection(
            index, "invalid_time", "time must be a finite number", raw
        )
    time_value = float(time_value)
    if not math.isfinite(time_value):
        return None, _rejection(
            index, "invalid_time", "time must be a finite number", raw
        )

    magnitude = raw.get("magnitude")
    magnitude_anomaly = None
    if magnitude is not None:
        if not _is_number(magnitude) or not math.isfinite(float(magnitude)):
            magnitude_anomaly = "invalid_magnitude"
            magnitude = None
        else:
            magnitude = float(magnitude)
            if abs(magnitude) > MAGNITUDE_ABS_LIMIT:
                magnitude_anomaly = "magnitude_out_of_range"

    record = {
        "station": station.strip(),
        "phase": phase,
        "time": time_value,
        "magnitude": magnitude,
        "magnitude_anomaly": magnitude_anomaly,
        "source_index": index,
        "raw_pick": json_safe(raw),
        "_normalised": True,
    }
    return record, None


def parse_clock_corrections(raw: Any) -> tuple[dict[str, float], list[dict]]:
    """Validate the clock-correction table.

    Invalid corrections are ignored (treated as zero) and surfaced as
    diagnostics.  A correction key matches only the station with exactly that
    name; there is deliberately no fuzzy matching.
    """

    if raw is None:
        return {}, []
    if not isinstance(raw, Mapping):
        raise ValueError("clock_corrections must be an object")

    corrections: dict[str, float] = {}
    diagnostics: list[dict] = []
    for station, value in raw.items():
        if not isinstance(station, str) or not station.strip():
            diagnostics.append(
                {
                    "kind": "invalid_clock_correction",
                    "station": str(station),
                    "raw_value": json_safe(value),
                    "detail": "station key must be a non-empty string; correction ignored",
                }
            )
            continue
        if not _is_number(value) or not math.isfinite(float(value)):
            diagnostics.append(
                {
                    "kind": "invalid_clock_correction",
                    "station": station,
                    "raw_value": json_safe(value),
                    "detail": "correction must be a finite number; correction ignored",
                }
            )
            continue
        corrections[station] = float(value)
    return corrections, diagnostics


def parse_payload(payload: Any) -> tuple[list[dict], list[dict], dict[str, float], list[dict]]:
    """Extract valid records, rejections, corrections and diagnostics."""

    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")

    rows = payload.get("picks", [])
    if rows is None:
        rows = []
    if not isinstance(rows, list):
        raise ValueError("picks must be a list")

    records: list[dict] = []
    rejected: list[dict] = []
    for index, raw in enumerate(rows):
        record, rejection = normalise_pick(raw, index)
        if record is not None:
            records.append(record)
        else:
            rejected.append(rejection)

    corrections, diagnostics = parse_clock_corrections(
        payload.get("clock_corrections", {})
    )
    return records, rejected, corrections, diagnostics


def load_records(payload: dict) -> list[dict]:
    """Backwards-compatible helper: return the associable records only."""

    records, _rejected, _corrections, _diagnostics = parse_payload(payload)
    return records
