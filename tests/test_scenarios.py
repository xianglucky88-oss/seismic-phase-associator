"""Regression pins for the scenarios described in docs/ and fixtures/scenarios/.

These tests do not exercise new association logic. They pin the observable
behaviour of the existing engine on the minimal documentation fixtures, so the
"fixture-verifiable" claims in docs/DATA_CONTRACT.md cannot silently drift.
"""
import json
import unittest
from pathlib import Path

from seismic_phase_associator.pipeline import run

ROOT = Path(__file__).parents[1]
SCENARIOS = ROOT / "fixtures" / "scenarios"


def summarise(scenario, name):
    payload = json.loads((SCENARIOS / scenario / f"{name}.json").read_text())
    result = run(payload)
    return {
        "status": result["status"],
        "events": [
            (e["event_id"], e["origin_time"], tuple(e["stations"]), e["pick_count"])
            for e in result["events"]
        ],
        "rejected": result["rejected_pick_count"],
    }


class TestScenarioPins(unittest.TestCase):
    # --- late pick -------------------------------------------------------
    def test_late_pick_before(self):
        self.assertEqual(
            summarise("late_pick", "before"),
            {"status": "ok",
             "events": [("evt-001", 10.0, ("STA01", "STA02"), 2)],
             "rejected": 0},
        )

    def test_late_pick_after_rewrites_origin_and_membership(self):
        # An earlier late pick moves origin_time backwards and evicts STA02.
        self.assertEqual(
            summarise("late_pick", "after"),
            {"status": "ok",
             "events": [("evt-001", 9.8, ("STA01", "STA03"), 2)],
             "rejected": 1},
        )

    # --- duplicate delivery ---------------------------------------------
    def test_exact_duplicate_is_only_counted(self):
        self.assertEqual(
            summarise("duplicate", "replay"),
            {"status": "ok",
             "events": [("evt-001", 10.0, ("STA01", "STA02"), 2)],
             "rejected": 1},
        )

    def test_offset_duplicate_creates_phantom_event(self):
        self.assertEqual(
            summarise("duplicate", "phantom_event"),
            {"status": "ok",
             "events": [
                 ("evt-001", 10.0, ("STA01", "STA02"), 2),
                 ("evt-002", 10.0, ("STA01", "STA02"), 2),
             ],
             "rejected": 0},
        )

    # --- manual retraction ----------------------------------------------
    def test_retraction_before(self):
        self.assertEqual(
            summarise("retraction", "before"),
            {"status": "ok",
             "events": [("evt-001", 10.0, ("STA01", "STA02", "STA03"), 3)],
             "rejected": 0},
        )

    def test_retraction_reduces_membership(self):
        self.assertEqual(
            summarise("retraction", "after"),
            {"status": "ok",
             "events": [("evt-001", 10.0, ("STA01", "STA03"), 2)],
             "rejected": 0},
        )

    def test_retraction_can_delete_event(self):
        self.assertEqual(
            summarise("retraction", "collapse"),
            {"status": "ok", "events": [], "rejected": 1},
        )

    # --- catalog merge ---------------------------------------------------
    def test_merge_sources_reuse_the_same_id(self):
        a = summarise("merge", "before_a")
        b = summarise("merge", "before_b")
        self.assertEqual(a["events"][0][0], "evt-001")
        self.assertEqual(b["events"][0][0], "evt-001")
        self.assertNotEqual(a["events"][0][1], b["events"][0][1])

    def test_merge_reassigns_second_event_id(self):
        # B's 20.0 s event was evt-001 alone, is evt-002 after the merge.
        self.assertEqual(
            summarise("merge", "merged"),
            {"status": "ok",
             "events": [
                 ("evt-001", 10.0, ("STA01", "STA02"), 2),
                 ("evt-002", 20.0, ("STA03", "STA04"), 2),
             ],
             "rejected": 0},
        )

    def test_merge_with_shared_station_drops_a_pick(self):
        self.assertEqual(
            summarise("merge", "shared_station"),
            {"status": "ok",
             "events": [("evt-001", 10.0, ("STA01", "STA02", "STA03"), 3)],
             "rejected": 1},
        )

    # --- station configuration change -----------------------------------
    def test_clock_correction_before(self):
        self.assertEqual(
            summarise("clock_correction", "before"),
            {"status": "ok",
             "events": [("evt-001", 10.0, ("STA01", "STA02", "STA03"), 3)],
             "rejected": 0},
        )

    def test_clock_correction_moves_origin(self):
        self.assertEqual(
            summarise("clock_correction", "shift_origin"),
            {"status": "ok",
             "events": [("evt-001", 10.1, ("STA01", "STA02", "STA03"), 3)],
             "rejected": 0},
        )

    def test_clock_correction_can_split_event(self):
        self.assertEqual(
            summarise("clock_correction", "split_event"),
            {"status": "ok", "events": [], "rejected": 2},
        )

    # --- greedy association ---------------------------------------------
    def test_greedy_drop_never_reconsiders_a_pick(self):
        # STA01 forms a rejected single event and is discarded even though a
        # later, accepted event exists.
        self.assertEqual(
            summarise("association", "greedy_drop"),
            {"status": "ok",
             "events": [("evt-002", 10.3, ("STA02", "STA03"), 2)],
             "rejected": 1},
        )

    def test_greedy_shift_redistributes_membership(self):
        # A 0.06 s input change republishes a different station pair.
        self.assertEqual(
            summarise("association", "greedy_shift"),
            {"status": "ok",
             "events": [("evt-001", 10.06, ("STA01", "STA02"), 2)],
             "rejected": 1},
        )


class TestContractInvariants(unittest.TestCase):
    """Invariants the docs rely on, checked against the current engine."""

    def test_pick_count_equals_station_count_when_accepted(self):
        payload = {"picks": [
            {"station": "A", "phase": "P", "time": 10.0},
            {"station": "A", "phase": "S", "time": 10.1},
            {"station": "B", "phase": "P", "time": 10.1},
        ]}
        for event in run(payload)["events"]:
            self.assertEqual(event["pick_count"], len(event["stations"]))
            self.assertEqual(event["stations"], event["source_stations"])

    def test_rejected_event_consumes_an_id(self):
        payload = {"picks": [
            {"station": "A", "phase": "P", "time": 1.0},
            {"station": "B", "phase": "P", "time": 5.0},
            {"station": "C", "phase": "P", "time": 5.1},
        ]}
        events = run(payload)["events"]
        self.assertEqual([e["event_id"] for e in events], ["evt-002"])

    def test_phase_is_not_used_for_association(self):
        p_payload = {"picks": [
            {"station": "A", "phase": "P", "time": 10.0},
            {"station": "B", "phase": "P", "time": 10.1},
        ]}
        s_payload = {"picks": [
            {"station": "A", "phase": "S", "time": 10.0},
            {"station": "B", "phase": "S", "time": 10.1},
        ]}
        self.assertEqual(
            [e["stations"] for e in run(p_payload)["events"]],
            [e["stations"] for e in run(s_payload)["events"]],
        )

    def test_magnitude_is_not_propagated(self):
        payload = {"picks": [
            {"station": "A", "phase": "P", "time": 10.0, "magnitude": 2.1},
            {"station": "B", "phase": "S", "time": 10.1, "magnitude": 5.0},
        ]}
        self.assertNotIn("magnitude", json.dumps(run(payload)))

    def test_non_dict_rows_are_dropped_before_counting(self):
        payload = {"picks": [
            {"station": "A", "phase": "P", "time": 10.0},
            "junk",
            {"station": "B", "phase": "S", "time": 10.1},
        ]}
        result = run(payload)
        self.assertEqual(len(result["events"]), 1)
        self.assertEqual(result["rejected_pick_count"], 0)

    def test_picks_must_be_a_list(self):
        with self.assertRaises(ValueError):
            run({"picks": "x"})

    def test_missing_picks_key_is_empty_input(self):
        self.assertEqual(
            run({}),
            {"project": "seismic-phase-associator", "status": "ok",
             "events": [], "rejected_pick_count": 0},
        )

    def test_missing_time_is_a_key_error(self):
        with self.assertRaises(KeyError):
            run({"picks": [{"station": "A", "phase": "P"}]})

    def test_non_numeric_correction_is_a_type_error(self):
        with self.assertRaises(TypeError):
            run({
                "picks": [
                    {"station": "A", "phase": "P", "time": 10.0},
                    {"station": "B", "phase": "S", "time": 10.1},
                ],
                "clock_corrections": {"A": "x"},
            })

    def test_null_corrections_is_an_attribute_error(self):
        with self.assertRaises(AttributeError):
            run({"picks": [{"station": "A", "phase": "P", "time": 10.0}],
                 "clock_corrections": None})

    def test_int_and_float_times_are_equivalent(self):
        # time is always coerced with float(), so int/float mix is harmless.
        as_int = run({"picks": [
            {"station": "A", "phase": "P", "time": 10},
            {"station": "B", "phase": "S", "time": 10},
        ]})
        as_float = run({"picks": [
            {"station": "A", "phase": "P", "time": 10.0},
            {"station": "B", "phase": "S", "time": 10.0},
        ]})
        self.assertEqual(as_int, as_float)

    def test_mixed_station_types_raise(self):
        with self.assertRaises(TypeError):
            run({"picks": [
                {"station": "A", "phase": "P", "time": 10.0},
                {"station": 2, "phase": "S", "time": 10.1},
            ]})

    def test_output_is_invariant_to_input_row_order(self):
        # The engine sorts by corrected time, so row order must not matter.
        import itertools

        picks = [
            {"station": "A", "phase": "P", "time": 10.0},
            {"station": "C", "phase": "S", "time": 10.0},
            {"station": "B", "phase": "P", "time": 10.26},
            {"station": "D", "phase": "S", "time": 10.5},
        ]
        baseline = json.dumps(run({"picks": picks}), sort_keys=True)
        for perm in itertools.permutations(picks):
            self.assertEqual(
                json.dumps(run({"picks": list(perm)}), sort_keys=True), baseline
            )


if __name__ == "__main__":
    unittest.main()
