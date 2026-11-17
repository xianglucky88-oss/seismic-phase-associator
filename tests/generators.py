"""Deterministic payload generators and minimal counterexample shrinking.

Only the standard library is used so the suite runs anywhere.  Randomness is
seeded, and every generated payload is JSON-serialisable and replayable.
"""

from __future__ import annotations

import json
import random
from copy import deepcopy

STATIONS = ["STA01", "STA02", "STA03", "STA04", "STA05"]
PHASES = ["P", "S"]
MAGNITUDES = [None, 0.5, 1.0, 2.1, 4.5, 9.9, 10.0, 10.1, -11.0]


def normal_payload(rng: random.Random, *, max_picks=6, corrections=True) -> dict:
    picks = []
    for _ in range(rng.randrange(0, max_picks + 1)):
        pick = {
            "station": rng.choice(STATIONS),
            "phase": rng.choice(PHASES),
            "time": round(rng.uniform(0.0, 2.0), rng.choice([0, 1, 2, 3])),
        }
        magnitude = rng.choice(MAGNITUDES)
        if magnitude is not None or rng.random() < 0.5:
            pick["magnitude"] = magnitude
        if rng.random() < 0.1:
            pick["label"] = "extra-%d" % rng.randrange(3)
        picks.append(pick)
    payload = {"picks": picks}
    if corrections and rng.random() < 0.7:
        payload["clock_corrections"] = {
            station: rng.choice([0.0, 0.05, -0.05, 0.25, -0.25])
            for station in STATIONS
            if rng.random() < 0.5
        }
    return payload


def messy_payload(rng: random.Random, *, max_picks=6) -> dict:
    """Payload with malformed records mixed into otherwise valid picks."""

    payload = normal_payload(rng, max_picks=max_picks)
    picks = payload["picks"]
    for _ in range(rng.randrange(0, 3)):
        picks.insert(
            rng.randrange(0, len(picks) + 1),
            rng.choice(
                [
                    None,
                    3,
                    [],
                    {"phase": "P", "time": 1.0},
                    {"station": "", "phase": "P", "time": 1.0},
                    {"station": "STA01", "phase": "X", "time": 1.0},
                    {"station": "STA01", "phase": "P", "time": None},
                    {"station": "STA01", "phase": "P", "time": "1.0"},
                    {"station": "STA01", "phase": "P", "time": True},
                ]
            ),
        )
    if rng.random() < 0.5:
        payload["clock_corrections"] = {
            "STA01": rng.choice([0.25, -0.25, "oops", None]),
            "STA02": 0.1,
        }
    return payload


def duplicate_payload(rng: random.Random, *, max_picks=6) -> dict:
    """Payload where some arrivals are delivered more than once."""

    payload = normal_payload(rng, max_picks=max_picks)
    picks = payload["picks"]
    for _ in range(rng.randrange(1, 4)):
        if not picks:
            break
        picks.append(deepcopy(rng.choice(picks)))
    rng.shuffle(picks)
    return payload


def all_generators():
    return {
        "normal": normal_payload,
        "messy": messy_payload,
        "duplicate": duplicate_payload,
    }


# ---------------------------------------------------------------------------
# shrinking
# ---------------------------------------------------------------------------


def _candidates(payload):
    """Yield structurally smaller payloads (one edit at a time)."""

    yield from _drop_pick_candidates(payload)
    yield from _simplify_pick_candidates(payload)
    yield from _simplify_correction_candidates(payload)


def _drop_pick_candidates(payload):
    picks = payload.get("picks")
    if not isinstance(picks, list):
        return
    for index in range(len(picks)):
        child = deepcopy(payload)
        del child["picks"][index]
        yield child


def _simplify_pick_candidates(payload):
    picks = payload.get("picks")
    if not isinstance(picks, list):
        return
    for index, pick in enumerate(picks):
        if not isinstance(pick, dict):
            continue
        for key in ("label", "magnitude"):
            if key in pick:
                child = deepcopy(payload)
                del child["picks"][index][key]
                yield child
        time = pick.get("time")
        if isinstance(time, float) and time != 0.0:
            for simpler in (0.0, round(time / 2.0, 3)):
                if simpler != time:
                    child = deepcopy(payload)
                    child["picks"][index]["time"] = simpler
                    yield child


def _simplify_correction_candidates(payload):
    corrections = payload.get("clock_corrections")
    if not isinstance(corrections, dict):
        return
    for station in list(corrections):
        child = deepcopy(payload)
        child["clock_corrections"].pop(station, None)
        yield child
    for station, value in corrections.items():
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value != 0:
            child = deepcopy(payload)
            child["clock_corrections"][station] = 0
            yield child


def shrink(payload, still_fails, *, max_steps=400):
    """Greedy 1-minimal shrinking.

    ``still_fails(candidate)`` must return ``True`` when the candidate still
    exhibits the property under investigation.  The result is a fixpoint: no
    single simplification keeps the failure.
    """

    current = deepcopy(payload)
    steps = 0
    improved = True
    while improved and steps < max_steps:
        improved = False
        for candidate in _candidates(current):
            steps += 1
            if still_fails(candidate):
                current = candidate
                improved = True
                break
    return current


def minimal_counterexample(payload, still_fails, *, max_steps=400):
    """Return ``(minimal_payload, replay_result_or_None)``."""

    minimal = shrink(payload, still_fails, max_steps=max_steps)
    try:
        return minimal, still_fails(minimal)
    except Exception:  # pragma: no cover - defensive
        return minimal, True


def describe(payload) -> str:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, default=repr)
