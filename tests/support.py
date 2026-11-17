"""Shared helpers for the association test suite."""

from __future__ import annotations

import sys
from pathlib import Path

# Allow ``python3 -m unittest discover -s tests`` to work from the repo root
# without an externally-set PYTHONPATH.
_SRC = Path(__file__).resolve().parents[1] / "src"
# Append, not insert: when a mutant tree is first on sys.path it must win.
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.append(str(_SRC))

import json
import random
from pathlib import Path

import oracle

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "input.json"
STATIONS = ["STA01", "STA02", "STA03", "STA04", "STA05"]


def load_fixture() -> dict:
    return json.loads(FIXTURE.read_text())


def pick(station, phase, time, **extra):
    item = {"station": station, "phase": phase, "time": time}
    item.update(extra)
    return item


def payload(picks, corrections=None):
    data = {"picks": list(picks)}
    if corrections is not None:
        data["clock_corrections"] = dict(corrections)
    return data


def events_only(result):
    """Events without delivery bookkeeping (source positions).

    Source indices necessarily change when the input is reordered; everything
    about the association itself must not.
    """

    return [
        {
            "event_id": event["event_id"],
            "origin_time": event["origin_time"],
            "stations": event["stations"],
            "phases": event["phases"],
            "pick_count": event["pick_count"],
            "picks": [
                (
                    item["station"],
                    item["phase"],
                    item["time"],
                    item["corrected_time"],
                    item["clock_correction"],
                    item["magnitude"],
                    item["magnitude_anomaly"],
                    json.dumps(item["raw_pick"], sort_keys=True),
                )
                for item in event["picks"]
            ],
        }
        for event in result["events"]
    ]


def association_view(result):
    """Event structure that must be invariant under equivalent time changes.

    Compares station/phase/corrected-time grouping and origin times, not the
    raw ``time``/``clock_correction`` split, which legitimately differs between
    "same corrected time reached by correction" and "reached by translation".
    """

    return [
        {
            "origin_time": event["origin_time"],
            "stations": event["stations"],
            "phases": event["phases"],
            "pick_count": event["pick_count"],
            "picks": [
                (
                    item["station"],
                    item["phase"],
                    item["corrected_time"],
                    item["magnitude"],
                    item["magnitude_anomaly"],
                )
                for item in event["picks"]
            ],
        }
        for event in result["events"]
    ]


def rejection_view(result):
    """Rejection reasons and corrected positions, raw-payload independent."""

    return sorted(
        (
            item["reason"],
            str(item.get("station")),
            str(item.get("phase")),
            item.get("corrected_time"),
        )
        for item in result["rejected_picks"]
    )


def rejection_reasons(result):
    return sorted(
        (
            item["reason"],
            json.dumps(item["raw_pick"], sort_keys=True, default=repr),
        )
        for item in result["rejected_picks"]
    )


def canonical(result):
    return {
        "events": events_only(result),
        "rejected": rejection_reasons(result),
        "summary": {
            key: value
            for key, value in result["summary"].items()
            if key not in {"duplicate_pick_count"}
        },
    }


def assert_matches_oracle(case, data, window=None):
    """Full differential assertion with a raw-pick-bearing failure message."""

    from seismic_phase_associator.pipeline import run

    expected = oracle.run(data) if window is None else oracle.run(data, window)
    actual = run(data) if window is None else run(data, window=window)
    diff = oracle.differences(expected, actual)
    if diff:
        case.fail(
            "implementation diverged from reference oracle:\n%s"
            % oracle.explain_diff(expected, actual, limit=12)
        )
    return actual, expected


def random_corrections(rng: random.Random, *, allow=STATIONS):
    return {
        station: rng.choice([0.0, 0.05, -0.05, 0.25, -0.25, 1.0])
        for station in allow
        if rng.random() < 0.6
    }


def run_variants(payloads):
    from seismic_phase_associator.pipeline import run

    return [run(data) for data in payloads]
