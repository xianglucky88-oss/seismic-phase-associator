"""Independent reference oracle for the seismic phase associator.

This module is a from-scratch implementation of the *documented* semantics in
``src/seismic_phase_associator``.  It deliberately imports nothing from the
package: it only uses the standard library.  The point is differential
testing — if the implementation and this oracle disagree on any input, at
least one of them is wrong, and the difference report names the raw picks that
caused it.

Documented semantics (kept in sync with engine.py's module docstring):

1. Validation
   * ``picks`` must be a list (or absent); each entry must be an object.
   * ``station`` / ``phase`` must be non-empty strings; phase is P or S
     (case-insensitive, surrounding whitespace stripped).
   * ``time`` must be a finite number.
   * bad picks never enter association and are reported with a reason plus the
     untouched raw pick.
   * ``clock_corrections`` maps station name -> finite number.  Invalid
     entries are ignored (zero) and diagnosed.  No fuzzy matching.
   * magnitudes outside [-10, 10] or non-finite are anomalies.  They are
     reported but never influence association.

2. Deduplication
   Repeated deliveries of ``(station, phase, time)`` collapse to one pick.
   The representative is the minimum under the canonical pick order, which
   ignores source order.  Repeats are reported with reason ``duplicate_pick``.

3. Association
   Effective time is ``time + correction[station]`` (exactly once, exact
   station match only).  Picks are processed in canonical order
   ``(effective time, station, phase, magnitude-presence, magnitude, raw)``.
   An event is anchored at the first pick's effective time; later picks join
   the earliest event whose ``|t_effective - origin| <= window`` (inclusive)
   and which does not already contain that ``(station, phase)``.  If no event
   qualifies a new event is created.  Events with fewer than two distinct
   stations are rejected as ``insufficient_stations``.

4. Ordering
   Accepted events are sorted by ``(origin, occupancy, member times, raw
   members)`` and numbered ``evt-NNN`` in that order.  Picks inside an event
   follow the canonical pick order.  Rejections, diagnostics and output keys
   are ordered deterministically; the report envelope is sorted JSON-safe.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow ``python3 -m unittest discover -s tests`` to work from the repo root
# without an externally-set PYTHONPATH.
_SRC = Path(__file__).resolve().parents[1] / "src"
# Append, not insert: when a mutant tree is first on sys.path it must win.
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.append(str(_SRC))

import copy
import json
import math

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


SUPPORTED_PHASES = {"P", "S"}
MAG_LIMIT = 10.0
DEFAULT_WINDOW = 0.25
MIN_STATIONS = 2
BOUNDARY_EPSILON = 1e-9


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=repr)


def _round(value):
    return round(float(value), 12)


def _sortable(value):
    if value is None:
        return (0, "")
    if isinstance(value, bool):
        return (1, str(value))
    if isinstance(value, (int, float)):
        return (2, repr(float(value)))
    return (3, str(value))


# ---------------------------------------------------------------------------
# 1. validation
# ---------------------------------------------------------------------------


def _invalid(index, reason, detail, raw):
    return {
        "source_index": index,
        "reason": reason,
        "detail": detail,
        "raw_pick": json_safe(raw),
    }


def _check_pick(raw, index):
    if not isinstance(raw, dict):
        return None, _invalid(index, "invalid_record", "pick must be an object", raw)

    station = raw.get("station")
    if not isinstance(station, str) or not station.strip():
        return None, _invalid(
            index, "missing_station", "station must be a non-empty string", raw
        )

    phase = raw.get("phase")
    if not isinstance(phase, str) or not phase.strip():
        return None, _invalid(
            index, "missing_phase", "phase must be a non-empty string", raw
        )
    phase = phase.strip().upper()
    if phase not in SUPPORTED_PHASES:
        return None, _invalid(
            index,
            "unsupported_phase",
            "phase must be P or S, got %r" % (raw.get("phase"),),
            raw,
        )

    if "time" not in raw or raw.get("time") is None:
        return None, _invalid(index, "missing_time", "time is required", raw)
    value = raw.get("time")
    if not _is_number(value) or not math.isfinite(float(value)):
        return None, _invalid(index, "invalid_time", "time must be a finite number", raw)

    magnitude = raw.get("magnitude")
    anomaly = None
    if magnitude is not None:
        if not _is_number(magnitude) or not math.isfinite(float(magnitude)):
            anomaly, magnitude = "invalid_magnitude", None
        else:
            magnitude = float(magnitude)
            if abs(magnitude) > MAG_LIMIT:
                anomaly = "magnitude_out_of_range"

    return {
        "station": station.strip(),
        "phase": phase,
        "time": float(value),
        "magnitude": magnitude,
        "magnitude_anomaly": anomaly,
        "source_index": index,
        "raw_pick": json_safe(raw),
        "_normalised": True,
    }, None


def parse_clock_corrections(raw):
    if raw is None:
        return {}, []
    if not isinstance(raw, dict):
        raise ValueError("clock_corrections must be an object")
    corrections, diagnostics = {}, []
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
        elif not _is_number(value) or not math.isfinite(float(value)):
            diagnostics.append(
                {
                    "kind": "invalid_clock_correction",
                    "station": station,
                    "raw_value": json_safe(value),
                    "detail": "correction must be a finite number; correction ignored",
                }
            )
        else:
            corrections[station] = float(value)
    return corrections, diagnostics


def parse_payload(payload):
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")
    rows = payload.get("picks", [])
    if rows is None:
        rows = []
    if not isinstance(rows, list):
        raise ValueError("picks must be a list")

    records, rejected = [], []
    for index, raw in enumerate(rows):
        record, rejection = _check_pick(raw, index)
        if record is None:
            rejected.append(rejection)
        else:
            records.append(record)
    corrections, diagnostics = parse_clock_corrections(
        payload.get("clock_corrections", {})
    )
    return records, rejected, corrections, diagnostics


# ---------------------------------------------------------------------------
# 2. effective times and ordering
# ---------------------------------------------------------------------------


def effective(record, corrections):
    return float(record["time"]) + float(corrections.get(record["station"], 0.0))


def pick_key(record, corrections):
    magnitude = record.get("magnitude")
    return (
        effective(record, corrections),
        record["station"],
        record["phase"],
        magnitude is None,
        magnitude if magnitude is not None else 0.0,
        _canonical(record.get("raw_pick")),
    )


def deduplicate(records, corrections):
    buckets = {}
    for record in records:
        key = (record["station"], record["phase"], float(record["time"]))
        buckets.setdefault(key, []).append(record)

    unique, rejected = [], []
    for key in sorted(buckets):
        members = sorted(buckets[key], key=lambda r: pick_key(r, corrections))
        chosen = members[0]
        survivor = dict(chosen)
        survivor["_corrections"] = corrections
        repeats = members[1:]
        survivor["duplicate_source_indices"] = sorted(
            m["source_index"] for m in repeats if m.get("source_index") is not None
        )
        survivor["duplicate_picks"] = [m.get("raw_pick") for m in repeats]
        survivor["delivery_count"] = len(members)
        unique.append(survivor)
        for member in repeats:
            rejected.append(
                {
                    "reason": "duplicate_pick",
                    "detail": "same station, phase and time already delivered; counted once",
                    "station": member.get("station"),
                    "phase": member.get("phase"),
                    "time": member.get("time"),
                    "corrected_time": _round(effective(member, corrections)),
                    "source_index": member.get("source_index"),
                    "raw_pick": member.get("raw_pick"),
                }
            )

    unique.sort(key=lambda r: pick_key(r, corrections))
    rejected.sort(key=rejection_key)
    return unique, rejected


def rejection_key(item):
    return (
        item["reason"],
        str(item.get("station")),
        str(item.get("phase")),
        item.get("corrected_time") or 0.0,
        _canonical(item.get("raw_pick")),
    )


# ---------------------------------------------------------------------------
# 3. association
# ---------------------------------------------------------------------------


def _insufficient(record, corrections):
    return {
        "reason": "insufficient_stations",
        "detail": "event would have fewer than %d distinct stations" % MIN_STATIONS,
        "station": record.get("station"),
        "phase": record.get("phase"),
        "time": record.get("time"),
        "corrected_time": _round(effective(record, corrections)),
        "source_index": record.get("source_index"),
        "raw_pick": record.get("raw_pick"),
    }


def associate(records, corrections, window=DEFAULT_WINDOW):
    records = [dict(r) for r in records]
    for record in records:
        record.setdefault("magnitude", None)
        record.setdefault("magnitude_anomaly", None)
        record.setdefault("source_index", None)
        record.setdefault("raw_pick", dict(record))

    unique, duplicate_rejections = deduplicate(records, corrections)

    events = []
    for record in unique:
        t = effective(record, corrections)
        eligible = [
            event
            for event in events
            if abs(t - event["origin"]) <= window + BOUNDARY_EPSILON
            and (record["station"], record["phase"]) not in event["slots"]
        ]
        if eligible:
            target = min(eligible, key=lambda e: e["origin"])
            target["members"].append(record)
            target["slots"].add((record["station"], record["phase"]))
        else:
            events.append(
                {
                    "origin": t,
                    "slots": {(record["station"], record["phase"])},
                    "members": [record],
                }
            )

    accepted, too_small = [], []
    for event in events:
        if len({m["station"] for m in event["members"]}) >= MIN_STATIONS:
            accepted.append(event)
        else:
            too_small.extend(event["members"])

    too_small.sort(key=lambda r: pick_key(r, corrections))
    accepted.sort(key=_event_signature)

    rejected = list(duplicate_rejections) + [
        _insufficient(record, corrections) for record in too_small
    ]
    rejected.sort(key=rejection_key)

    events_out = []
    for index, event in enumerate(accepted, start=1):
        members = sorted(event["members"], key=lambda r: pick_key(r, corrections))
        events_out.append(
            {
                "event_id": "evt-%03d" % index,
                "origin_time": _round(event["origin"]),
                "stations": sorted({m["station"] for m in members}),
                "source_stations": sorted({m["station"] for m in members}),
                "phases": sorted({m["phase"] for m in members}),
                "pick_count": len(members),
                "picks": [_format_pick(m, corrections) for m in members],
            }
        )

    return {
        "events": events_out,
        "rejected_picks": rejected,
        "rejected_pick_count": len(rejected),
        "summary": {
            "input_pick_count": len(records),
            "unique_pick_count": len(unique),
            "duplicate_pick_count": len(duplicate_rejections),
            "accepted_event_count": len(events_out),
            "accepted_pick_count": sum(e["pick_count"] for e in events_out),
            "rejected_pick_count": len(rejected),
            "insufficient_station_pick_count": len(too_small),
            "magnitude_anomaly_count": sum(
                1
                for e in events_out
                for p in e["picks"]
                if p["magnitude_anomaly"] is not None
            ),
            "window": float(window),
            "min_stations_per_event": MIN_STATIONS,
        },
    }


def _event_signature(event):
    return (
        _round(event["origin"]),
        tuple(sorted(event["slots"])),
        tuple(
            sorted(
                (m["station"], m["phase"], _round(float(m["time"])))
                for m in event["members"]
            )
        ),
        _canonical([m.get("raw_pick") for m in event["members"]]),
    )


def _format_pick(record, corrections):
    correction = float(corrections.get(record["station"], 0.0))
    return {
        "station": record["station"],
        "phase": record["phase"],
        "time": record["time"],
        "corrected_time": _round(float(record["time"]) + correction),
        "clock_correction": correction,
        "magnitude": record.get("magnitude"),
        "magnitude_anomaly": record.get("magnitude_anomaly"),
        "source_index": record.get("source_index"),
        "duplicate_source_indices": sorted(record.get("duplicate_source_indices", [])),
        "duplicate_picks": list(record.get("duplicate_picks", [])),
        "delivery_count": record.get("delivery_count", 1),
        "raw_pick": record.get("raw_pick"),
    }


# ---------------------------------------------------------------------------
# 4. pipeline + report envelope
# ---------------------------------------------------------------------------


def _parse_rejection(item):
    raw = item["raw_pick"]
    return {
        "reason": item["reason"],
        "detail": item["detail"],
        "station": raw.get("station") if isinstance(raw, dict) else None,
        "phase": raw.get("phase") if isinstance(raw, dict) else None,
        "time": raw.get("time") if isinstance(raw, dict) else None,
        "source_index": item["source_index"],
        "raw_pick": item["raw_pick"],
    }


def _envelope(project, data):
    data = copy.deepcopy(data)
    events = []
    for event in sorted(
        data.get("events", []),
        key=lambda e: (
            e.get("origin_time") if e.get("origin_time") is not None else 0.0,
            tuple(e.get("stations") or ()),
            tuple(
                (p.get("station"), p.get("phase"), p.get("corrected_time"))
                for p in e.get("picks", ())
            ),
        ),
    ):
        picks = sorted(
            event.get("picks", []),
            key=lambda p: (
                p.get("corrected_time")
                if p.get("corrected_time") is not None
                else 0.0,
                str(p.get("station")),
                str(p.get("phase")),
                str(p.get("source_index")),
                repr(p.get("raw_pick")),
            ),
        )
        event["picks"] = picks
        event["stations"] = sorted(set(event.get("stations") or ()))
        event["source_stations"] = sorted(set(event.get("source_stations") or ()))
        event["phases"] = sorted(set(event.get("phases") or ()))
        event["pick_count"] = len(picks)
        events.append(event)
    data["events"] = events
    data["rejected_picks"] = sorted(
        data.get("rejected_picks", []),
        key=lambda item: (
            str(item.get("reason")),
            str(item.get("station")),
            str(item.get("phase")),
            repr(item.get("time")),
            str(item.get("source_index")),
            repr(item.get("raw_pick")),
        ),
    )
    data["rejected_pick_count"] = len(data["rejected_picks"])
    summary = dict(data.get("summary", {}))
    summary["rejected_pick_count"] = data["rejected_pick_count"]
    summary["accepted_pick_count"] = sum(e["pick_count"] for e in events)
    summary["accepted_event_count"] = len(events)
    data["summary"] = summary
    return {"project": project, "status": "ok", "schema_version": 1, **data}


def run(payload, window=DEFAULT_WINDOW):
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")
    records, rejected, corrections, diagnostics = parse_payload(payload)
    data = associate(records, corrections, window=window)

    parse_rejections = sorted(
        [_parse_rejection(item) for item in rejected],
        key=lambda item: (
            item["reason"],
            _sortable(item.get("station")),
            _sortable(item.get("phase")),
            _sortable(item.get("time")),
            _sortable(item.get("source_index")),
            repr(item.get("raw_pick")),
        ),
    )
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
    return _envelope("seismic-phase-associator", data)


# ---------------------------------------------------------------------------
# diffing helpers (used by tests to explain failures)
# ---------------------------------------------------------------------------


def canonical_view(result):
    """Order- and envelope-insensitive structural view of a result."""

    return {
        "events": [
            {
                "event_id": event["event_id"],
                "origin_time": event["origin_time"],
                "stations": event["stations"],
                "picks": [
                    (
                        pick["station"],
                        pick["phase"],
                        pick["time"],
                        pick["corrected_time"],
                        pick.get("magnitude"),
                    )
                    for pick in event["picks"]
                ],
            }
            for event in result["events"]
        ],
        "rejected": sorted(
            (
                item["reason"],
                _canonical(item.get("raw_pick")),
            )
            for item in result["rejected_picks"]
        ),
    }


def differences(expected, actual, path="root", context_raw=None):
    """Recursively list differences, always attributing them to a raw pick.

    ``context_raw`` is the nearest enclosing pick's ``raw_pick``; leaf
    differences such as a wrong ``corrected_time`` inherit it so a report can
    quote the original pick rather than only a float.
    """

    if isinstance(expected, dict) and "raw_pick" in expected:
        context_raw = expected.get("raw_pick", context_raw)
    elif isinstance(actual, dict) and "raw_pick" in actual:
        context_raw = actual.get("raw_pick", context_raw)

    # An event-level difference is caused by the event's member picks, so
    # narrow the evidence from the root fallback to the actual members.
    for candidate in (expected, actual):
        if isinstance(candidate, dict) and candidate.get("picks"):
            members = [
                pick.get("raw_pick")
                for pick in candidate["picks"]
                if isinstance(pick, dict) and "raw_pick" in pick
            ]
            if members:
                context_raw = members

    def raw_of(value):
        return _raw_of(value) or context_raw

    out = []
    if type(expected) is not type(actual) and not (
        isinstance(expected, (int, float))
        and not isinstance(expected, bool)
        and isinstance(actual, (int, float))
        and not isinstance(actual, bool)
    ):
        out.append(
            {
                "path": path,
                "expected": expected,
                "actual": actual,
                "raw_pick": raw_of(expected) or raw_of(actual),
            }
        )
        return out
    if isinstance(expected, dict):
        for key in sorted(set(expected) | set(actual), key=str):
            if key not in expected:
                out.append(
                    {
                        "path": "%s.%s" % (path, key),
                        "expected": "<missing>",
                        "actual": actual[key],
                        "raw_pick": raw_of(actual[key]),
                    }
                )
            elif key not in actual:
                out.append(
                    {
                        "path": "%s.%s" % (path, key),
                        "expected": expected[key],
                        "actual": "<missing>",
                        "raw_pick": raw_of(expected[key]),
                    }
                )
            else:
                out.extend(
                    differences(
                        expected[key], actual[key], "%s.%s" % (path, key), context_raw
                    )
                )
        return out
    if isinstance(expected, (list, tuple)):
        if len(expected) != len(actual):
            out.append(
                {
                    "path": path + ".length",
                    "expected": len(expected),
                    "actual": len(actual),
                    "raw_pick": raw_of(expected) or raw_of(actual),
                }
            )
        for index, (left, right) in enumerate(zip(expected, actual)):
            out.extend(
                differences(left, right, "%s[%d]" % (path, index), context_raw)
            )
        return out
    if expected != actual:
        out.append(
            {
                "path": path,
                "expected": expected,
                "actual": actual,
                "raw_pick": raw_of(expected) or raw_of(actual),
            }
        )
    return out


def _raw_of(value):
    if isinstance(value, dict):
        if "raw_pick" in value:
            return value["raw_pick"]
        for key in ("picks", "rejected_picks"):
            if key in value:
                return None
    if isinstance(value, list):
        for item in value:
            raw = _raw_of(item)
            if raw is not None:
                return raw
    return None


def explain_diff(expected, actual, limit=5):
    """Human-readable diff summary that always points at raw picks."""

    found = differences(expected, actual)[:limit]
    lines = []
    for diff in found:
        lines.append(
            "%s: expected=%r actual=%r raw_pick=%r"
            % (diff["path"], diff["expected"], diff["actual"], diff["raw_pick"])
        )
    if not found:
        lines.append("no differences")
    return "\n".join(lines)
