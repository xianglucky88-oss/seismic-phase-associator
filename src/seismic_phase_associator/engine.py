"""Domain calculation for phase association.

The engine is a pure function of normalised records plus a clock-correction
table.  It has no knowledge of JSON, files or reporting; the pipeline wires
those together.

Association semantics
---------------------
* A pick's *effective* time is ``time + clock_corrections[station]``.  A
  correction is looked up by exact station name and is applied exactly once,
  to that station's picks only.
* Two picks are the *same arrival* when ``(station, phase, time)`` agree.
  Repeats are collapsed before association, so a repeated delivery can never
  be counted twice and never creates a phantom station.
* Picks are considered in the canonical order
  ``(effective_time, station, phase, magnitude, raw payload)`` so that
  permuting the input cannot change the grouping.
* An event is anchored at its earliest effective time.  A pick joins an
  existing event when ``|effective_time - origin_time| <= window`` (inclusive
  boundary) and that ``(station, phase)`` is not already present in the event.
  Anchoring at the minimum prevents window chaining.  P and S from the same
  station may share an event; a second pick with the same station *and* phase
  may not.
* Only events with at least two distinct stations are accepted.  Everything
  else is reported as an isolated pick with an explicit reason.
* Accepted events are sorted by ``(origin_time, station/phase signature)`` and
  numbered only after sorting, so IDs are stable under input permutation.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from typing import Any, Iterable, Mapping

DEFAULT_WINDOW = 0.25
MIN_STATIONS_PER_EVENT = 2

# Tolerances are expressed in the same decimal grid as the inputs.  A pick
# exactly on the boundary is inside the window; a pick one unit of the last
# decimal place outside it is outside.  This avoids binary-float surprises
# such as 0.1 + 0.2 > 0.3.
BOUNDARY_EPSILON = 1e-9


def effective_time(record: Mapping[str, Any], corrections: Mapping[str, float]) -> float:
    return float(record["time"]) + float(corrections.get(record["station"], 0.0))


def _canonical_value(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=repr)


def _pick_sort_key(record: Mapping[str, Any], corrections: Mapping[str, float]) -> tuple:
    magnitude = record.get("magnitude")
    return (
        effective_time(record, corrections),
        record["station"],
        record["phase"],
        magnitude is None,
        magnitude if magnitude is not None else 0.0,
        _canonical_value(record.get("raw_pick")),
    )


def _dedup_key(record: Mapping[str, Any]) -> tuple:
    return (record["station"], record["phase"], float(record["time"]))


def _canonical_duplicate(
    records: Iterable[Mapping[str, Any]],
    corrections: Mapping[str, float],
) -> Mapping[str, Any]:
    """Deterministically choose the representative of a duplicate group.

    Source index is deliberately *not* part of the choice so that reordering
    equivalent deliveries does not change which one survives.
    """

    return min(records, key=lambda r: _pick_sort_key(r, corrections))


def _rejection(
    record: Mapping[str, Any],
    reason: str,
    detail: str,
    corrections: Mapping[str, float],
) -> dict:
    return {
        "reason": reason,
        "detail": detail,
        "station": record.get("station"),
        "phase": record.get("phase"),
        "time": record.get("time"),
        "corrected_time": round(effective_time(record, corrections), 12),
        "source_index": record.get("source_index"),
        "raw_pick": record.get("raw_pick"),
    }


def deduplicate(
    records: Iterable[Mapping[str, Any]],
    corrections: Mapping[str, float],
) -> tuple[list[dict], list[dict]]:
    """Collapse repeated deliveries of the same arrival.

    Returns ``(unique_records, duplicate_rejections)``.  The surviving record
    is annotated with the number and source indices of the repeats so the
    report can point back at every delivered copy.
    """

    groups: dict[tuple, list[Mapping[str, Any]]] = defaultdict(list)
    for record in records:
        groups[_dedup_key(record)].append(record)

    unique: list[dict] = []
    duplicates: list[dict] = []
    for key in sorted(groups):
        members = sorted(groups[key], key=lambda r: _pick_sort_key(r, corrections))
        chosen = _canonical_duplicate(members, corrections)
        survivor = dict(chosen)
        survivor["_corrections"] = corrections
        repeats = [m for m in members if m is not chosen]
        survivor["duplicate_source_indices"] = sorted(
            (m.get("source_index") for m in repeats if m.get("source_index") is not None)
        )
        survivor["duplicate_picks"] = [m.get("raw_pick") for m in repeats]
        survivor["delivery_count"] = len(members)
        unique.append(survivor)
        for member in repeats:
            duplicates.append(
                _rejection(
                    member,
                    "duplicate_pick",
                    "same station, phase and time already delivered; counted once",
                    corrections,
                )
            )

    unique.sort(key=lambda r: _pick_sort_key(r, corrections))
    duplicates.sort(
        key=lambda item: (
            item["reason"],
            str(item.get("station")),
            str(item.get("phase")),
            item.get("corrected_time") or 0.0,
            _canonical_value(item.get("raw_pick")),
        )
    )
    return unique, duplicates


def _event_signature(event: Mapping[str, Any]) -> tuple:
    return (
        round(event["origin_time"], 12),
        tuple(sorted(event["_occupancy"])),
        tuple(
            sorted(
                (
                    p["station"],
                    p["phase"],
                    round(float(p["time"]), 12),
                )
                for p in event["_members"]
            )
        ),
        _canonical_value([p.get("raw_pick") for p in event["_members"]]),
    )


def _build_events(
    unique: list[dict],
    corrections: Mapping[str, float],
    window: float,
) -> tuple[list[dict], list[dict]]:
    events: list[dict] = []
    too_small: list[dict] = []

    for record in unique:
        pick_time = effective_time(record, corrections)
        candidates = [
            event
            for event in events
            if abs(pick_time - event["origin_time"]) <= window + BOUNDARY_EPSILON
            and (record["station"], record["phase"]) not in event["_occupancy"]
        ]
        # Anchoring on the earliest qualifying origin prevents window chaining
        # from silently drifting a cluster forward in time.
        match = min(candidates, key=lambda e: e["origin_time"]) if candidates else None
        if match is None:
            events.append(
                {
                    "origin_time": pick_time,
                    "_occupancy": {(record["station"], record["phase"])},
                    "_members": [record],
                }
            )
        else:
            match["_members"].append(record)
            match["_occupancy"].add((record["station"], record["phase"]))

    accepted: list[dict] = []
    for event in events:
        stations = {p["station"] for p in event["_members"]}
        if len(stations) >= MIN_STATIONS_PER_EVENT:
            accepted.append(event)
        else:
            too_small.extend(event["_members"])

    too_small.sort(key=lambda r: _pick_sort_key(r, corrections))
    accepted.sort(key=_event_signature)
    return accepted, too_small


def _format_pick(record: Mapping[str, Any], corrections: Mapping[str, float]) -> dict:
    correction = float(corrections.get(record["station"], 0.0))
    return {
        "station": record["station"],
        "phase": record["phase"],
        "time": record["time"],
        "corrected_time": round(float(record["time"]) + correction, 12),
        "clock_correction": correction,
        "magnitude": record.get("magnitude"),
        "magnitude_anomaly": record.get("magnitude_anomaly"),
        "source_index": record.get("source_index"),
        "duplicate_source_indices": sorted(record.get("duplicate_source_indices", [])),
        "duplicate_picks": list(record.get("duplicate_picks", [])),
        "delivery_count": record.get("delivery_count", 1),
        "raw_pick": record.get("raw_pick"),
    }


def calculate(
    rows: Iterable[Mapping[str, Any]],
    payload: Mapping[str, Any] | None = None,
    *,
    window: float = DEFAULT_WINDOW,
    corrections: Mapping[str, float] | None = None,
) -> dict:
    """Associate picks into events.

    ``rows`` may be normalised parser records or plain dictionaries with at
    least ``station``, ``phase`` and ``time``.  When ``payload`` is supplied,
    its ``clock_corrections`` are used unless explicit ``corrections`` are
    given.  This keeps the module directly testable while preserving the
    original calling convention.
    """

    payload = payload or {}
    if corrections is None:
        corrections = payload.get("clock_corrections", {}) or {}
    if not isinstance(corrections, Mapping):
        raise ValueError("clock_corrections must be an object")
    # Direct engine callers bypass the parser; never let a hostile correction
    # value turn into a crash or a non-finite time.  Invalid entries are
    # treated as zero (the parser reports them when it sees them).
    corrections = {
        station: float(value)
        for station, value in corrections.items()
        if isinstance(station, str)
        and isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    }
    if not isinstance(window, (int, float)) or isinstance(window, bool) or window < 0:
        raise ValueError("window must be a non-negative number")
    window = float(window)

    records = [dict(row) for row in rows]
    for record in records:
        # Plain-dictionary callers may not have gone through the parser.
        record.setdefault("magnitude", None)
        record.setdefault("magnitude_anomaly", None)
        record.setdefault("source_index", None)
        record.setdefault("raw_pick", dict(record))

    unique, duplicates = deduplicate(records, corrections)
    accepted, too_small = _build_events(unique, corrections, window)

    rejected: list[dict] = list(duplicates)
    for record in too_small:
        rejected.append(
            _rejection(
                record,
                "insufficient_stations",
                "event would have fewer than %d distinct stations"
                % MIN_STATIONS_PER_EVENT,
                corrections,
            )
        )

    rejected.sort(
        key=lambda item: (
            item["reason"],
            str(item.get("station")),
            str(item.get("phase")),
            item.get("corrected_time") or 0.0,
            _canonical_value(item.get("raw_pick")),
        )
    )

    output_events: list[dict] = []
    for index, event in enumerate(accepted, start=1):
        members = sorted(event["_members"], key=lambda r: _pick_sort_key(r, corrections))
        stations = sorted({p["station"] for p in members})
        phases = sorted({p["phase"] for p in members})
        output_events.append(
            {
                "event_id": "evt-%03d" % index,
                "origin_time": round(event["origin_time"], 12),
                "stations": stations,
                "source_stations": stations,
                "phases": phases,
                "pick_count": len(members),
                "picks": [_format_pick(p, corrections) for p in members],
            }
        )

    accepted_pick_count = sum(e["pick_count"] for e in output_events)
    anomaly_count = sum(
        1
        for e in output_events
        for p in e["picks"]
        if p["magnitude_anomaly"] is not None
    )

    return {
        "events": output_events,
        "rejected_picks": rejected,
        "rejected_pick_count": len(rejected),
        "summary": {
            "input_pick_count": len(records),
            "unique_pick_count": len(unique),
            "duplicate_pick_count": len(duplicates),
            "accepted_event_count": len(output_events),
            "accepted_pick_count": accepted_pick_count,
            "rejected_pick_count": len(rejected),
            "insufficient_station_pick_count": len(too_small),
            "magnitude_anomaly_count": anomaly_count,
            "window": window,
            "min_stations_per_event": MIN_STATIONS_PER_EVENT,
        },
    }
