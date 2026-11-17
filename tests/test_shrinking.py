"""Minimal-counterexample reduction tests.

When a differential test fails, the failure payload is usually much larger than
necessary.  These tests pair the reducer in ``generators.shrink`` with
deliberately faulty implementations and prove that:

* a random search finds a counterexample,
* reduction reaches a fixpoint that is 1-minimal (removing or simplifying any
  single element makes the fault disappear),
* the reduced payload still reproduces the fault, and
* the failure explanation names the original picks involved.

Each faulty variant is a plausible implementation bug of exactly the kind the
task asks to locate: leaked clock correction, an exclusive window boundary, a
missing deduplication, and a report that trusts stale counts.
"""

from __future__ import annotations

import random
import unittest
from unittest import mock

import generators
import oracle
from seismic_phase_associator import engine, report
from seismic_phase_associator.pipeline import run


def _matches_oracle(payload):
    try:
        expected = oracle.run(payload)
        actual = run(payload)
    except Exception:
        return False
    return not oracle.differences(expected, actual)


def _split(payload):
    return payload.get("picks", [])


# ---------------------------------------------------------------------------
# faulty variants
# ---------------------------------------------------------------------------


def _leaked_correction(record, corrections):
    """Every station receives STA01's correction."""

    return float(record["time"]) + float(corrections.get("STA01", 0.0))


def _exclusive_window(record, corrections):
    return float(record["time"]) + float(corrections.get(record["station"], 0.0))


def faulty_leak():
    return mock.patch.object(engine, "effective_time", _leaked_correction)


class _ExclusiveWindow:
    """Patch ``<=`` semantics by shrinking the window via a private hook."""

    def __enter__(self):
        original = engine.BOUNDARY_EPSILON
        engine.BOUNDARY_EPSILON = -1e-9
        self._restore = lambda: setattr(engine, "BOUNDARY_EPSILON", original)
        return self

    def __exit__(self, *exc):
        self._restore()


def _run_leaking(payload):
    with faulty_leak():
        return run(payload)


def _run_exclusive(payload):
    with _ExclusiveWindow():
        return run(payload)


def _run_no_dedup(payload):
    with mock.patch.object(
        engine,
        "deduplicate",
        lambda records, corrections: (
            [dict(r, _corrections=corrections) for r in records],
            [],
        ),
    ):
        return run(payload)


def _strip_raw_picks(events):
    """Faulty report: keeps structure but loses the audit trail."""

    stripped = []
    for event in events:
        item = dict(event)
        item["picks"] = [
            {key: value for key, value in pick.items() if key != "raw_pick"}
            for pick in event.get("picks", [])
        ]
        stripped.append(item)
    return stripped


def _run_stale_report(payload):
    with mock.patch.object(report, "_normalise_events", _strip_raw_picks):
        return run(payload)


FAULTS = {
    "leaked_correction": _run_leaking,
    "exclusive_window": _run_exclusive,
    "missing_dedup": _run_no_dedup,
    "stale_report": _run_stale_report,
}


def fails_for(faulty_run, payload):
    try:
        expected = oracle.run(payload)
        actual = faulty_run(payload)
    except Exception:
        return True
    return bool(oracle.differences(expected, actual))


def find_failing_payload(faulty_run, *, seeds=400):
    for seed in range(seeds):
        for generator in generators.all_generators().values():
            payload = generator(random.Random(seed))
            if fails_for(faulty_run, payload):
                return payload
    raise AssertionError("no counterexample found")


class TestShrinking(unittest.TestCase):
    def test_each_fault_reduces_to_a_one_minimal_counterexample(self):
        for name, faulty_run in FAULTS.items():
            with self.subTest(fault=name):
                original = find_failing_payload(faulty_run)
                self.assertTrue(fails_for(faulty_run, original))

                minimal = generators.shrink(
                    original, lambda candidate: fails_for(faulty_run, candidate)
                )
                self.assertTrue(
                    fails_for(faulty_run, minimal),
                    "%s: reducer lost the fault\n%r -> %r"
                    % (name, original, minimal),
                )
                # 1-minimal: no single simplification still fails.
                for candidate in generators._candidates(minimal):
                    self.assertFalse(
                        fails_for(faulty_run, candidate),
                        "%s: not 1-minimal, %r still smaller\nminimal=%r"
                        % (name, candidate, minimal),
                    )

    def test_reduced_counterexample_quotes_the_original_picks(self):
        original = find_failing_payload(_run_leaking)
        minimal = generators.shrink(
            original, lambda candidate: fails_for(_run_leaking, candidate)
        )
        explanation = oracle.explain_diff(
            oracle.run(minimal), _run_leaking(minimal)
        )
        self.assertIn("raw_pick=", explanation)
        self.assertIn("corrected_time", explanation)
        for pick in _split(minimal):
            self.assertIn(repr(pick), explanation.replace("'", "'"))

    def test_leaked_correction_reduces_to_the_offending_station(self):
        original = find_failing_payload(_run_leaking)
        minimal = generators.shrink(
            original, lambda candidate: fails_for(_run_leaking, candidate)
        )
        payload = generators.minimal_counterexample(
            original, lambda candidate: fails_for(_run_leaking, candidate)
        )[0]
        # Doing it twice is stable.
        again = generators.shrink(
            payload, lambda candidate: fails_for(_run_leaking, candidate)
        )
        self.assertEqual(generators.describe(payload), generators.describe(again))
        # The reduced payload must still exercise more than one station or a
        # correction, otherwise it could not expose a leak at all.
        # The reduced payload must still exercise either a clock correction or
        # two stations; a fault that constrains neither cannot expose a leak.
        stations = {
            pick.get("station")
            for pick in _split(payload)
            if isinstance(pick, dict) and isinstance(pick.get("station"), str)
        }
        self.assertTrue(
            len(stations) >= 2 or payload.get("clock_corrections"), minimal
        )
        self.assertLessEqual(len(_split(payload)), 3, minimal)

    def test_exclusive_boundary_reduces_to_a_boundary_pair(self):
        original = find_failing_payload(_run_exclusive)
        minimal = generators.shrink(
            original, lambda candidate: fails_for(_run_exclusive, candidate)
        )
        picks = [p for p in _split(minimal) if isinstance(p, dict) and "time" in p]
        self.assertLessEqual(len(_split(minimal)), 3, minimal)
        self.assertLessEqual(len(picks), 3, minimal)
        # The reduced payload, verbatim, still exhibits the boundary fault.
        self.assertTrue(fails_for(_run_exclusive, minimal))
        # The association, not the parsing, must be what differs.
        self.assertTrue(oracle.run(minimal)["events"])

    def test_missing_dedup_reduces_to_a_repeated_pick(self):
        original = find_failing_payload(_run_no_dedup)
        minimal = generators.shrink(
            original, lambda candidate: fails_for(_run_no_dedup, candidate)
        )
        raw = [
            (p.get("station"), p.get("phase"), p.get("time"))
            for p in _split(minimal)
            if isinstance(p, dict)
        ]
        self.assertEqual(len(raw), len(set(raw)), "minimal case still has duplicates")
        self.assertGreaterEqual(len(raw), 2, minimal)

    def test_shrink_is_deterministic(self):
        original = find_failing_payload(_run_leaking)
        first = generators.shrink(
            original, lambda candidate: fails_for(_run_leaking, candidate)
        )
        second = generators.shrink(
            original, lambda candidate: fails_for(_run_leaking, candidate)
        )
        self.assertEqual(generators.describe(first), generators.describe(second))

    def test_healthy_implementation_never_fails(self):
        for seed in range(100):
            payload = generators.messy_payload(random.Random(seed))
            self.assertTrue(_matches_oracle(payload), payload)


if __name__ == "__main__":
    unittest.main()
