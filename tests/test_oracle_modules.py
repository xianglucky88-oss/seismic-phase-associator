"""Reference-oracle differential tests, one module boundary at a time.

The oracle in ``tests/oracle.py`` is an independent implementation of the
documented semantics.  These tests exercise parser, engine, pipeline and report
separately, then verify the composed pipeline, so a divergence can be attributed
to a specific boundary instead of just "the pipeline".
"""

from __future__ import annotations

import copy
import json
import random
import unittest

import generators
import oracle
import support
from seismic_phase_associator import engine, parser, pipeline, report


class TestParserOracle(unittest.TestCase):
    def test_normalisation_contract(self):
        raw = [
            {"station": "  A  ", "phase": " p ", "time": 1, "magnitude": 2.5},
            {"station": "B", "phase": "S", "time": 1.25, "magnitude": 11},
            {"station": "C", "phase": "P", "time": "1.0"},
            {"station": "", "phase": "P", "time": 1.0},
            {"station": "D", "phase": "PPP", "time": 1.0},
            {"station": "E", "phase": "P", "time": float("nan")},
            {"station": "F", "phase": "P"},
            "not-a-pick",
            None,
        ]
        actual = parser.parse_payload({"picks": raw})
        expected = oracle.parse_payload({"picks": raw})
        diff = oracle.differences(expected, actual)
        self.assertFalse(diff, oracle.explain_diff(expected, actual))

    def test_parser_keeps_raw_pick_for_every_rejection(self):
        raw = [
            {"station": "A", "phase": "P", "time": 1.0, "note": "keep-me"},
            {"station": "A", "phase": "Q", "time": 1.0},
            {"station": "B", "phase": "P"},
            {"phase": "P", "time": 1.0},
            {"station": "C", "phase": "P", "time": 1.0, "magnitude": [1, 2]},
        ]
        records, rejected, corrections, diagnostics = parser.parse_payload(
            {"picks": raw}
        )
        self.assertEqual([r["station"] for r in records], ["A", "C"])
        self.assertEqual(
            [r["reason"] for r in rejected],
            ["unsupported_phase", "missing_time", "missing_station"],
        )
        for item in rejected:
            self.assertIn("raw_pick", item)
            self.assertEqual(item["raw_pick"], raw[item["source_index"]])
        self.assertEqual(records[0]["raw_pick"], raw[0])
        # An anomalous magnitude is attached to the record, not silently lost.
        self.assertEqual(records[1]["magnitude_anomaly"], "invalid_magnitude")

    def test_parser_corrections_are_strictly_exact_station_matches(self):
        corrections, diagnostics = parser.parse_clock_corrections(
            {"A": 0.1, " a ": 0.2, "B": "x", "C": float("inf")}
        )
        self.assertEqual(corrections, {"A": 0.1, " a ": 0.2})
        self.assertEqual({d["station"] for d in diagnostics}, {"B", "C"})

    def test_parser_total_on_generated_mess(self):
        for seed in range(300):
            data = generators.messy_payload(random.Random(seed))
            expected = oracle.parse_payload(data)
            actual = parser.parse_payload(data)
            diff = oracle.differences(expected, actual)
            self.assertFalse(
                diff, "seed %d %s\n%s" % (seed, data, oracle.explain_diff(expected, actual))
            )


class TestEngineOracle(unittest.TestCase):
    def calculate(self, data, **kwargs):
        records = parser.load_records(data)
        corrections = data.get("clock_corrections", {})
        return engine.calculate(records, corrections=corrections, **kwargs)

    def test_hand_built_association_semantics(self):
        cases = [
            # Inclusive window boundary.
            support.payload([support.pick("A", "P", 0.0), support.pick("B", "P", 0.25)]),
            # One tick beyond the boundary.
            support.payload([support.pick("A", "P", 0.0), support.pick("B", "P", 0.2501)]),
            # P and S from one station may share an event.
            support.payload(
                [
                    support.pick("A", "P", 1.0),
                    support.pick("A", "S", 1.1),
                    support.pick("B", "P", 1.05),
                ]
            ),
            # A second same-phase pick cannot occupy the same slot.
            support.payload(
                [
                    support.pick("A", "P", 1.0),
                    support.pick("A", "P", 1.05),
                    support.pick("B", "P", 1.02),
                ]
            ),
            # Window chaining: C is within 0.25 of B but not of A.
            support.payload(
                [
                    support.pick("A", "P", 0.0),
                    support.pick("B", "P", 0.24),
                    support.pick("C", "P", 0.48),
                ]
            ),
        ]
        for data in cases:
            with self.subTest(case=support.canonical(oracle.run(data))):
                records, _rejected, corrections, _diagnostics = oracle.parse_payload(data)
                expected = oracle.associate(records, corrections)
                actual = self.calculate(data)
                diff = oracle.differences(expected, actual)
                self.assertFalse(diff, oracle.explain_diff(expected, actual))

    def test_engine_matches_oracle_on_generated_corpora(self):
        for name, generator in generators.all_generators().items():
            for seed in range(80):
                data = generator(random.Random(seed))
                records, _rejected, corrections, _diagnostics = oracle.parse_payload(data)
                expected = oracle.associate(records, corrections)
                actual = engine.calculate(records, corrections=corrections)
                diff = oracle.differences(expected, actual)
                self.assertFalse(
                    diff,
                    "%s seed %d %s\n%s"
                    % (
                        name,
                        seed,
                        json.dumps(data, default=repr),
                        oracle.explain_diff(expected, actual),
                    ),
                )

    def test_picks_inside_event_follow_corrected_time(self):
        data = support.payload(
            [
                support.pick("A", "P", 0.5),
                support.pick("B", "P", 0.1),
                support.pick("C", "P", 0.2),
            ],
            {"A": -0.25, "B": 0.0, "C": 0.0},
        )
        result = self.calculate(data)
        self.assertEqual(len(result["events"]), 1)
        picks = result["events"][0]["picks"]
        self.assertEqual([p["station"] for p in picks], ["B", "C", "A"])
        self.assertEqual([p["corrected_time"] for p in picks], [0.1, 0.2, 0.25])


class TestPipelineOracle(unittest.TestCase):
    def test_every_rejection_names_the_raw_pick(self):
        data = {
            "picks": [
                support.pick("A", "P", 1.0),
                support.pick("A", "P", 1.0),
                support.pick("B", "P", 1.5),
                {"station": "C", "phase": "P"},
                "garbage",
            ],
            "clock_corrections": {"A": "bad"},
        }
        result = pipeline.run(data)
        reasons = [item["reason"] for item in result["rejected_picks"]]
        self.assertIn("duplicate_pick", reasons)
        self.assertIn("insufficient_stations", reasons)
        self.assertIn("missing_time", reasons)
        self.assertIn("invalid_record", reasons)
        for item in result["rejected_picks"]:
            self.assertIn("raw_pick", item)
            self.assertIn("detail", item)
        self.assertEqual(result["diagnostics_count"], 1)
        self.assertEqual(result["diagnostics"][0]["raw_value"], "bad")

    def test_parse_rejections_and_engine_rejections_combine(self):
        data = support.payload(
            [
                support.pick("A", "P", 1.0),
                support.pick("B", "P", 1.1),
                support.pick("A", "P", 1.0),
                support.pick("C", "P", 5.0),
                {"station": "Z", "phase": "P", "time": "later"},
            ]
        )
        expected = oracle.run(data)
        actual = pipeline.run(data)
        diff = oracle.differences(expected, actual)
        self.assertFalse(diff, oracle.explain_diff(expected, actual))
        self.assertEqual(
            actual["rejected_pick_count"], len(actual["rejected_picks"])
        )
        # One accepted A+B event; rejections = duplicate A, isolated C,
        # unparsable Z.
        self.assertEqual(len(actual["events"]), 1)
        self.assertEqual(actual["events"][0]["stations"], ["A", "B"])
        self.assertEqual(
            sorted(item["reason"] for item in actual["rejected_picks"]),
            ["duplicate_pick", "insufficient_stations", "invalid_time"],
        )
        self.assertEqual(actual["rejected_pick_count"], 3)
        # Every rejection and every acceptance keeps the raw pick.
        for item in actual["rejected_picks"]:
            self.assertIn("raw_pick", item)
        for item in actual["events"][0]["picks"]:
            self.assertIn("raw_pick", item)

    def test_non_dict_and_non_list_payloads_are_explicit_errors(self):
        for bad in ([], "x", 3, None):
            with self.assertRaises(ValueError):
                pipeline.run(bad)
        with self.assertRaises(ValueError):
            pipeline.run({"picks": {}})
        with self.assertRaises(ValueError):
            pipeline.run({"picks": [], "clock_corrections": []})


class TestReportOracle(unittest.TestCase):
    def test_report_is_deterministic_and_json_safe(self):
        for seed in range(120):
            data = generators.messy_payload(random.Random(seed))
            first = pipeline.run(data)
            rendered = report.stable_json(first)
            reloaded = json.loads(rendered)
            self.assertEqual(report.stable_json(reloaded), rendered)
            self.assertEqual(pipeline.run(copy.deepcopy(data)), first)

    def test_report_preserves_raw_pick_identity_in_events_and_rejections(self):
        data = support.payload(
            [
                support.pick("A", "P", 1.0, magnitude=2.0, marker={"x": [1]}),
                support.pick("B", "P", 1.1, marker={"x": [2]}),
                support.pick("A", "P", 1.0),
                {"station": "C", "phase": "P", "time": "nope", "marker": {"x": [3]}},
            ]
        )
        result = pipeline.run(data)
        self.assertEqual(
            [event["picks"][0]["raw_pick"]["marker"] for event in result["events"]],
            [{"x": [1]}],
        )
        rejected_raw = [item["raw_pick"] for item in result["rejected_picks"]]
        self.assertIn({"station": "C", "phase": "P", "time": "nope", "marker": {"x": [3]}}, rejected_raw)
        self.assertIn({"station": "A", "phase": "P", "time": 1.0}, rejected_raw)

    def test_report_sorts_events_and_picks(self):
        data = support.payload(
            [
                support.pick("D", "P", 5.0),
                support.pick("C", "P", 5.1),
                support.pick("B", "S", 1.2),
                support.pick("A", "P", 1.0),
            ]
        )
        result = pipeline.run(data)
        self.assertEqual(
            [e["event_id"] for e in result["events"]], ["evt-001", "evt-002"]
        )
        self.assertEqual([e["origin_time"] for e in result["events"]], [1.0, 5.0])
        for event in result["events"]:
            times = [p["corrected_time"] for p in event["picks"]]
            self.assertEqual(times, sorted(times))
            self.assertEqual(event["stations"], sorted(event["stations"]))


if __name__ == "__main__":
    unittest.main()
