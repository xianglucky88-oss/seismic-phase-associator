"""Conservation and isolation invariants.

These properties must hold for *every* payload, independently of the oracle:

* **Conservation** — every delivered row is accounted for exactly once: either
  inside an accepted event, or in exactly one rejection.  Nothing is invented
  and nothing disappears (this is the "no event double counting" guarantee).
* **Isolation reasons** — every unassociable pick carries one explicit,
  documented reason, and the reason matches the pick's actual defect.
* **Uniqueness of decisions** — no row index appears twice in the audit trail.
* **Monotone audit trail** — every accepted pick reports the raw payload it
  came from, unchanged.
"""

from __future__ import annotations

import random
import unittest

import generators
import support
from seismic_phase_associator.pipeline import run

KNOWN_REASONS = {
    "invalid_record",
    "missing_station",
    "missing_phase",
    "unsupported_phase",
    "missing_time",
    "invalid_time",
    "duplicate_pick",
    "insufficient_stations",
}


def _indices_in_events(result):
    return [
        pick["source_index"]
        for event in result["events"]
        for pick in event["picks"]
    ]


def _indices_in_rejections(result):
    return [item["source_index"] for item in result["rejected_picks"]]


class TestConservation(unittest.TestCase):
    def test_every_row_is_accounted_for_exactly_once(self):
        for name, generator in generators.all_generators().items():
            for seed in range(60):
                data = generator(random.Random(seed))
                if not isinstance(data.get("picks"), list):
                    continue
                result = run(data)
                total = len(data["picks"])
                accepted = _indices_in_events(result)
                rejected = _indices_in_rejections(result)

                self.assertEqual(
                    len(accepted) + len(rejected),
                    total,
                    "%s seed %d: accepted %d + rejected %d != input %d\n%r"
                    % (name, seed, len(accepted), len(rejected), total, data),
                )
                self.assertEqual(
                    sorted(accepted + rejected),
                    sorted(range(total)),
                    "%s seed %d: source indices not a permutation of the rows"
                    % (name, seed),
                )
                self.assertEqual(
                    result["summary"]["accepted_pick_count"], len(accepted)
                )
                self.assertEqual(
                    result["summary"]["rejected_pick_count"], len(rejected)
                )

    def test_no_source_index_is_reported_twice(self):
        for seed in range(80):
            data = generators.messy_payload(random.Random(seed))
            result = run(data)
            indices = _indices_in_events(result) + _indices_in_rejections(result)
            self.assertEqual(
                len(indices), len(set(indices)), "row double-counted: %r" % data
            )

    def test_event_pick_count_matches_pick_list_and_distinct_stations(self):
        for seed in range(80):
            data = generators.normal_payload(random.Random(seed), max_picks=8)
            result = run(data)
            for event in result["events"]:
                self.assertEqual(event["pick_count"], len(event["picks"]))
                self.assertEqual(
                    event["stations"],
                    sorted({pick["station"] for pick in event["picks"]}),
                )
                self.assertGreaterEqual(len(event["stations"]), 2)
                slots = [(p["station"], p["phase"]) for p in event["picks"]]
                self.assertEqual(
                    len(slots), len(set(slots)), "slot occupied twice: %r" % event
                )

    def test_each_pick_belongs_to_at_most_one_event(self):
        for seed in range(60):
            data = generators.normal_payload(random.Random(seed), max_picks=8)
            result = run(data)
            all_slots = set()
            for event in result["events"]:
                for pick in event["picks"]:
                    self.assertNotIn(
                        pick["source_index"],
                        all_slots,
                        "pick %r appears in more than one event"
                        % pick["raw_pick"],
                    )
                    all_slots.add(pick["source_index"])


class TestIsolationReasons(unittest.TestCase):
    def test_reasons_are_from_the_documented_set(self):
        for seed in range(100):
            data = generators.messy_payload(random.Random(seed))
            result = run(data)
            for item in result["rejected_picks"]:
                self.assertIn(item["reason"], KNOWN_REASONS, item)
                self.assertTrue(item["detail"], item)
                self.assertIn("raw_pick", item, item)

    def test_reason_matches_defect(self):
        cases = [
            (None, "invalid_record"),
            ({"station": "A", "phase": "P", "time": 1.0}, None),
            ({"station": "A", "phase": "P"}, "missing_time"),
            ({"station": "A", "phase": "P", "time": "x"}, "invalid_time"),
            ({"phase": "P", "time": 1.0}, "missing_station"),
            ({"station": "A", "time": 1.0}, "missing_phase"),
            ({"station": "A", "phase": "Q", "time": 1.0}, "unsupported_phase"),
        ]
        for raw, expected in cases:
            result = run({"picks": [raw]})
            reasons = [item["reason"] for item in result["rejected_picks"]]
            if expected is None:
                self.assertNotIn("invalid_record", reasons)
                # A lone station pick is still isolated, just at engine level.
                self.assertEqual(reasons, ["insufficient_stations"])
            else:
                self.assertIn(expected, reasons, (raw, reasons))

    def test_unassociable_pick_is_never_silently_dropped(self):
        data = support.payload(
            [
                support.pick("A", "P", 1.0),
                support.pick("Q", "P", 100.0),
                {"station": "B", "phase": "P", "time": "soon"},
            ]
        )
        result = run(data)
        rejected = result["rejected_picks"]
        self.assertEqual(len(rejected), 3)
        self.assertEqual(
            sorted(item["reason"] for item in rejected),
            ["insufficient_stations", "insufficient_stations", "invalid_time"],
        )
        isolated = [
            item["raw_pick"]
            for item in rejected
            if item["reason"] == "insufficient_stations"
        ]
        # Both A (lone station) and Q (far away) are isolated, each with the
        # exact row that could not be associated.
        self.assertIn(support.pick("Q", "P", 100.0), isolated)
        self.assertIn(support.pick("A", "P", 1.0), isolated)


class TestRawPayloadFidelity(unittest.TestCase):
    def test_accepted_pick_exposes_exact_raw_payload(self):
        for seed in range(50):
            data = generators.normal_payload(random.Random(seed), max_picks=8)
            result = run(data)
            for event in result["events"]:
                for pick in event["picks"]:
                    index = pick["source_index"]
                    self.assertEqual(
                        pick["raw_pick"],
                        data["picks"][index],
                        "raw payload diverged for source index %d" % index,
                    )

    def test_correction_only_touches_its_own_station(self):
        data = support.payload(
            [
                support.pick("A", "P", 1.0),
                support.pick("B", "P", 1.02),
                support.pick("C", "P", 1.04),
            ],
            {"B": 0.02},
        )
        result = run(data)
        corrections = {
            pick["station"]: pick["clock_correction"]
            for event in result["events"]
            for pick in event["picks"]
        }
        self.assertEqual(corrections, {"A": 0.0, "B": 0.02, "C": 0.0})
        times = {
            pick["station"]: pick["corrected_time"]
            for event in result["events"]
            for pick in event["picks"]
        }
        self.assertEqual(times["A"], 1.0)
        self.assertEqual(times["B"], 1.04)
        self.assertEqual(times["C"], 1.04)


if __name__ == "__main__":
    unittest.main()
