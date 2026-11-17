"""Standalone differential probe used by mutation testing.

Exit code 0 means the implementation under test agrees with the reference
oracle on a deterministic corpus of hand-built and generated payloads.  Any
disagreement prints a minimal, raw-pick-bearing explanation and exits 1.

Run with the package ``src`` directory and this ``tests`` directory on
``sys.path``::

    PYTHONPATH=src:tests python3 tests/differential_probe.py
"""

from __future__ import annotations

import json
import random
import sys

import oracle
from generators import all_generators

# Hand-built cases pin the semantics that random generation rarely hits.
HAND_BUILT = [
    {"picks": []},
    {},
    {"picks": None},
    {
        "picks": [
            {"station": "A", "phase": "P", "time": 10.02, "magnitude": 2.1},
            {"station": "B", "phase": "S", "time": 10.15},
            {"station": "C", "phase": "P", "time": 10.10},
        ],
        "clock_corrections": {"A": -0.02, "B": 0.0, "C": 0.01},
    },
    {
        # Exactly on the inclusive boundary.
        "picks": [
            {"station": "A", "phase": "P", "time": 0.0},
            {"station": "B", "phase": "P", "time": 0.25},
        ],
    },
    {
        # One tick past the boundary must not join.
        "picks": [
            {"station": "A", "phase": "P", "time": 0.0},
            {"station": "B", "phase": "P", "time": 0.2501},
        ],
    },
    {
        # Corrections move B onto the boundary, A away from it.
        "picks": [
            {"station": "A", "phase": "P", "time": 0.0},
            {"station": "B", "phase": "P", "time": 0.20},
        ],
        "clock_corrections": {"A": 1.0, "B": 0.05},
    },
    {
        # Repeated arrival: counted once, never a phantom second station.
        "picks": [
            {"station": "A", "phase": "P", "time": 0.0},
            {"station": "A", "phase": "P", "time": 0.0},
            {"station": "B", "phase": "P", "time": 0.1},
        ],
    },
    {
        # Same time, different stations/phases.
        "picks": [
            {"station": "A", "phase": "P", "time": 1.0},
            {"station": "B", "phase": "P", "time": 1.0},
            {"station": "A", "phase": "S", "time": 1.0},
        ],
    },
    {
        # Same station and phase, distinct times: only one may occupy a slot.
        "picks": [
            {"station": "A", "phase": "P", "time": 1.00},
            {"station": "A", "phase": "P", "time": 1.05},
            {"station": "B", "phase": "P", "time": 1.02},
        ],
    },
    {
        # Missing station and malformed rows are isolated with reasons.
        "picks": [
            {"phase": "P", "time": 0.0},
            {"station": "B", "phase": "P", "time": 0.1},
            "nope",
            None,
        ],
    },
    {
        # Abnormal magnitudes are reported, not used for grouping.
        "picks": [
            {"station": "A", "phase": "P", "time": 0.0, "magnitude": 99.0},
            {"station": "B", "phase": "P", "time": 0.1, "magnitude": "bad"},
            {"station": "C", "phase": "P", "time": 0.2, "magnitude": None},
        ],
    },
    {
        # Uncorrelated station receives no correction.
        "picks": [
            {"station": "A", "phase": "P", "time": 0.0},
            {"station": "B", "phase": "P", "time": 0.4},
            {"station": "C", "phase": "P", "time": 0.41},
        ],
        "clock_corrections": {"A": 0.0, "B": -0.2},
    },
    {
        "picks": [
            {"station": "A", "phase": "P", "time": 1.0},
            {"station": "B", "phase": "S", "time": 1.2},
        ],
        "clock_corrections": {"A": "oops", "B": None},
    },
]

CORPORA = {"hand_built": lambda _rng: list(HAND_BUILT)}


def corpus(seed):
    rng = random.Random(seed)
    payloads = list(HAND_BUILT)
    for generator in all_generators().values():
        payloads.extend(generator(rng) for _ in range(120))
    return payloads


def contract_checks():
    """Module-level contracts that the end-to-end comparison cannot isolate.

    ``report.build_result`` must independently stabilise ordering and counts
    even if it is handed engine data whose lists were shuffled.  That is the
    module's actual contract, so it is tested directly rather than inferred
    from the engine's already-sorted output.
    """

    import copy

    from seismic_phase_associator.report import build_result

    pick_a = {
        "station": "B",
        "phase": "S",
        "time": 1.2,
        "corrected_time": 1.2,
        "source_index": 1,
        "raw_pick": {"station": "B", "phase": "S", "time": 1.2},
    }
    pick_b = {
        "station": "A",
        "phase": "P",
        "time": 1.0,
        "corrected_time": 1.0,
        "source_index": 0,
        "raw_pick": {"station": "A", "phase": "P", "time": 1.0},
    }
    event_late = {
        "event_id": "evt-002",
        "origin_time": 5.0,
        "stations": ["D", "C"],
        "source_stations": ["D", "C"],
        "phases": ["P"],
        "pick_count": 99,
        "picks": [
            {
                "station": "D",
                "phase": "P",
                "time": 5.0,
                "corrected_time": 5.0,
                "source_index": 3,
                "raw_pick": {"station": "D", "phase": "P", "time": 5.0},
            },
            {
                "station": "C",
                "phase": "P",
                "time": 5.01,
                "corrected_time": 5.01,
                "source_index": 2,
                "raw_pick": {"station": "C", "phase": "P", "time": 5.01},
            },
        ],
    }
    event_early = {
        "event_id": "evt-001",
        "origin_time": 1.0,
        "stations": ["B", "A"],
        "source_stations": ["B", "A"],
        "phases": ["S", "P"],
        "pick_count": 99,
        "picks": [pick_a, pick_b],
    }
    data = {
        "events": [event_late, event_early],
        "rejected_picks": [
            {"reason": "duplicate_pick", "station": "Z", "phase": "P", "time": 2.0},
            {"reason": "duplicate_pick", "station": "A", "phase": "P", "time": 2.0},
        ],
        "summary": {"rejected_pick_count": 99},
    }
    original = copy.deepcopy(data)
    result = build_result("x", data)

    problems = []
    if data != original:
        problems.append("build_result mutated its input")
    if [e["event_id"] for e in result["events"]] != ["evt-001", "evt-002"]:
        # IDs are preserved but events must be ordered by origin/signature.
        problems.append(
            "events not re-sorted by origin: %r"
            % [e["event_id"] for e in result["events"]]
        )
    if [p["station"] for p in result["events"][0]["picks"]] != ["A", "B"]:
        problems.append("early event picks not sorted by corrected time")
    if [p["station"] for p in result["events"][1]["picks"]] != ["D", "C"]:
        problems.append("late event picks not sorted by corrected time")
    for event in result["events"]:
        if event["pick_count"] != len(event["picks"]):
            problems.append(
                "pick_count %r != len(picks) %r for %s"
                % (event["pick_count"], len(event["picks"]), event["event_id"])
            )
        for pick in event["picks"]:
            if "raw_pick" not in pick:
                problems.append("reported pick missing raw_pick: %r" % (pick,))
    if [r["station"] for r in result["rejected_picks"]] != ["A", "Z"]:
        problems.append("rejected picks not sorted")
    if result["rejected_pick_count"] != 2:
        problems.append("rejected_pick_count stale")
    if result["summary"]["rejected_pick_count"] != 2:
        problems.append("summary.rejected_pick_count stale")

    # JSON round-trip must preserve the audit trail exactly.
    rendered = json.dumps(result, sort_keys=True, allow_nan=False)
    if json.loads(rendered) != result:
        problems.append("report is not JSON round-trip stable")

    return problems


def check(payloads):
    from seismic_phase_associator.pipeline import run

    for payload in payloads:
        try:
            expected = oracle.run(payload)
        except Exception as exc:  # oracle must be total on this corpus
            raise AssertionError(
                "oracle raised on %r: %s" % (payload, exc)
            ) from exc
        try:
            actual = run(payload)
        except Exception as exc:
            return "implementation raised %s: %s on %s" % (
                type(exc).__name__,
                exc,
                json.dumps(payload, default=repr),
            )
        diff = oracle.differences(expected, actual)
        if diff:
            return "diff on %s\n%s" % (
                json.dumps(payload, default=repr),
                oracle.explain_diff(expected, actual),
            )
    return None


def main() -> int:
    problem = check(corpus(20261117))
    if problem:
        print(problem)
        return 1
    problems = contract_checks()
    if problems:
        print("report contract violations:\n  " + "\n  ".join(problems))
        return 1
    print("ok: implementation matches reference oracle")
    return 0


if __name__ == "__main__":
    sys.exit(main())
