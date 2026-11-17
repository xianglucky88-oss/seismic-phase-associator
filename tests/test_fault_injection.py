"""Fault-injection tests.

Two kinds of faults are injected:

1. **Data faults** — hostile values in the raw payload.  The contract is that
   the pipeline never crashes with an opaque exception, always isolates the
   offending pick with a reason and its raw payload, and still agrees with the
   reference oracle.
2. **Boundary faults** — deliberate breakage of one module's output to prove
   that the differential harness has teeth and that the neighbouring module
   either compensates deterministically or surfaces the fault.  These are the
   tests that locate *which* boundary a drift comes from.
"""

from __future__ import annotations

import random
import unittest
from unittest import mock

import oracle
import support
from seismic_phase_associator import engine, parser, pipeline, report


class TestPayloadFaults(unittest.TestCase):
    CASES = {
        "nan_time": [{"station": "A", "phase": "P", "time": float("nan")}],
        "inf_time": [{"station": "A", "phase": "P", "time": float("inf")}],
        "bool_time": [{"station": "A", "phase": "P", "time": True}],
        "string_time": [{"station": "A", "phase": "P", "time": "1.0"}],
        "none_time": [{"station": "A", "phase": "P", "time": None}],
        "list_magnitude": [{"station": "A", "phase": "P", "time": 1.0, "magnitude": [1]}],
        "huge_magnitude": [{"station": "A", "phase": "P", "time": 1.0, "magnitude": 1e308}],
        "weird_station": [{"station": "\u0000\n", "phase": "P", "time": 1.0}],
        "whitespace_station": [{"station": "   ", "phase": "P", "time": 1.0}],
        "numeric_station": [{"station": 7, "phase": "P", "time": 1.0}],
        "unknown_phase": [{"station": "A", "phase": "PKP", "time": 1.0}],
        "nested_raw": [
            {
                "station": "A",
                "phase": "P",
                "time": 1.0,
                "extra": {"deep": [1, {"x": None}]},
            }
        ],
        "tuple_like": [{"station": "A", "phase": "P", "time": 1.0, "magnitude": None}],
    }

    def test_each_fault_is_isolated_or_normalised_with_a_raw_pick(self):
        for name, picks in self.CASES.items():
            with self.subTest(fault=name):
                data = {"picks": picks + [support.pick("B", "P", 1.1)]}
                result = pipeline.run(data)  # must not raise
                expected = oracle.run(data)
                diff = oracle.differences(expected, result)
                self.assertFalse(diff, "%s: %s" % (name, oracle.explain_diff(expected, result)))
                for item in result["rejected_picks"]:
                    self.assertIn("raw_pick", item, name)
                    self.assertIn("reason", item, name)
                    self.assertTrue(item["detail"], name)
                # JSON-safe even for non-finite floats.
                report.stable_json(result)

    def test_bad_correction_table_never_changes_association_shape(self):
        base = support.payload(
            [support.pick("A", "P", 1.0), support.pick("B", "P", 1.1)]
        )
        good = pipeline.run(base)
        for bad_corrections in (
            {"A": "oops"},
            {"A": None},
            {"A": float("nan")},
            {"A": [0.1]},
            {1: 0.1},
            {"A": True},
        ):
            with self.subTest(corrections=bad_corrections):
                result = pipeline.run(dict(base, clock_corrections=bad_corrections))
                self.assertEqual(
                    support.events_only(good), support.events_only(result)
                )
                self.assertEqual(result["diagnostics_count"], len(result["diagnostics"]))
                for item in result["diagnostics"]:
                    self.assertIn("raw_value", item)

    def test_engine_direct_caller_with_hostile_corrections_cannot_crash(self):
        rows = [
            {"station": "A", "phase": "P", "time": 0.0},
            {"station": "B", "phase": "P", "time": 0.1},
            {"station": "C", "phase": "P", "time": 0.05},
        ]
        for corrections in (
            {"A": "oops"},
            {"A": float("nan")},
            {"A": float("inf")},
            {1: 5},
            {"A": True},
            None,
        ):
            with self.subTest(corrections=corrections):
                data = engine.calculate(rows, corrections=corrections or {})
                self.assertEqual(len(data["events"]), 1)
                report.stable_json(data)

    def test_payload_shape_faults_are_typed_errors(self):
        for bad in ([], (), 0, "payload", None, object()):
            with self.assertRaises(ValueError):
                pipeline.run(bad)
        for bad_picks in ({}, "abc", 5, True):
            with self.assertRaises(ValueError):
                pipeline.run({"picks": bad_picks})
        for bad_corrections in ([], "x", 5, True):
            with self.assertRaises(ValueError):
                pipeline.run({"picks": [], "clock_corrections": bad_corrections})

    def test_large_batch_has_no_drift(self):
        rng = random.Random(42)
        picks = [
            support.pick(
                "S%02d" % rng.randrange(20),
                rng.choice(["P", "S"]),
                round(rng.uniform(0.0, 20.0), 3),
                magnitude=rng.choice([1.0, 2.0, 11.0]),
            )
            for _ in range(4000)
        ]
        data = {"picks": picks}
        result = pipeline.run(data)
        expected = oracle.run(data)
        diff = oracle.differences(expected, result)
        self.assertFalse(diff, oracle.explain_diff(expected, result))
        self.assertEqual(
            result["summary"]["input_pick_count"], len(picks)
        )
        self.assertEqual(
            result["summary"]["accepted_pick_count"]
            + result["summary"]["rejected_pick_count"],
            len(picks),
        )
        # Reversing a large batch must not move any event.
        reversed_result = pipeline.run(dict(data, picks=list(reversed(picks))))
        self.assertEqual(
            support.association_view(result),
            support.association_view(reversed_result),
        )


class TestBoundaryFaults(unittest.TestCase):
    """Inject faults at module boundaries and verify they are detected."""

    def test_parser_dropping_a_record_is_detected(self):
        real = parser.load_records

        def lossy(payload):
            rows = real(payload)
            return rows[:-1] if len(rows) > 1 else rows

        data = support.payload(
            [support.pick("A", "P", 1.0), support.pick("B", "P", 1.1)]
        )
        expected = oracle.run(data)
        with mock.patch.object(
            pipeline, "parse_payload", lambda p: (lossy(p), [], {}, [])
        ):
            actual = pipeline.run(data)
        diff = oracle.differences(expected, actual)
        self.assertTrue(diff, "silently dropped pick was not detected by the oracle")

    def test_engine_origin_drift_is_detected(self):
        real = engine.calculate

        def drifting(rows, payload=None, **kwargs):
            data = real(rows, payload, **kwargs)
            for event in data["events"]:
                event["origin_time"] = round(event["origin_time"] + 0.01, 12)
            return data

        data = support.payload(
            [support.pick("A", "P", 1.0), support.pick("B", "P", 1.1)]
        )
        expected = oracle.run(data)
        with mock.patch.object(pipeline, "calculate", drifting):
            actual = pipeline.run(data)
        self.assertTrue(oracle.differences(expected, actual))

    def test_report_corruption_is_detected(self):
        data = support.payload(
            [support.pick("A", "P", 1.0), support.pick("B", "P", 1.1)]
        )
        expected = oracle.run(data)
        with mock.patch.object(
            pipeline, "build_result", lambda project, body: {"project": project}
        ):
            actual = pipeline.run(data)
        self.assertTrue(oracle.differences(expected, actual))

    def test_report_repairs_stale_counts_and_ordering(self):
        # A boundary fault at engine->report: hand the report deliberately
        # stale counts and shuffled lists.  Reporting must not trust them.
        pick_a = {
            "station": "A",
            "phase": "P",
            "time": 1.0,
            "corrected_time": 1.0,
            "raw_pick": {"station": "A", "phase": "P", "time": 1.0},
        }
        pick_b = {
            "station": "B",
            "phase": "P",
            "time": 1.1,
            "corrected_time": 1.1,
            "raw_pick": {"station": "B", "phase": "P", "time": 1.1},
        }
        body = {
            "events": [
                {
                    "event_id": "evt-001",
                    "origin_time": 1.0,
                    "stations": ["B", "A", "A"],
                    "source_stations": ["B", "A"],
                    "phases": ["S", "P"],
                    "pick_count": 0,
                    "picks": [pick_b, pick_a],
                }
            ],
            "rejected_picks": [],
            "summary": {"accepted_pick_count": -5},
        }
        result = report.build_result("x", body)
        event = result["events"][0]
        self.assertEqual(event["pick_count"], 2)
        self.assertEqual(event["stations"], ["A", "B"])
        self.assertEqual([p["station"] for p in event["picks"]], ["A", "B"])
        self.assertEqual(result["summary"]["accepted_pick_count"], 2)
        self.assertEqual(body["events"][0]["pick_count"], 0, "report mutated input")


class TestOracleTeeth(unittest.TestCase):
    """The oracle must reject plausible-but-wrong behaviours."""

    def _expect_detected(self, mutate):
        data = support.payload(
            [
                support.pick("A", "P", 1.0),
                support.pick("B", "P", 1.2),
                support.pick("C", "P", 1.21),
            ],
            {"B": 0.05},
        )
        expected = oracle.run(data)
        actual = oracle.run(data)
        mutate(actual)
        return oracle.differences(expected, actual)

    def test_detects_dropped_pick(self):
        self.assertTrue(self._expect_detected(lambda r: r["events"][0]["picks"].pop()))

    def test_detects_changed_correction(self):
        def mutate(result):
            result["events"][0]["picks"][0]["corrected_time"] += 0.001

        self.assertTrue(self._expect_detected(mutate))

    def test_detects_extra_event(self):
        self.assertTrue(
            self._expect_detected(
                lambda r: r["events"].append(dict(r["events"][0], event_id="evt-999"))
            )
        )

    def test_diff_reports_raw_pick(self):
        diff = self._expect_detected(
            lambda r: r["events"][0]["picks"][0].update(corrected_time=999.0)
        )
        self.assertTrue(diff)
        self.assertTrue(
            any(item.get("raw_pick") for item in diff),
            "differences() must always surface the raw pick",
        )


if __name__ == "__main__":
    unittest.main()
