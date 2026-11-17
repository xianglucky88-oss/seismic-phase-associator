"""Metamorphic tests.

Relationship-based tests: rather than re-deriving the answer, they assert how
the output must change when the input is transformed in a semantics-preserving
way.  Each transformation is paired with the property it must satisfy.

Covers: input reordering/permutation, repeated delivery of the same pick,
splitting a batch into sub-batches, adding isolated stations, and duplicating
the whole payload.
"""

from __future__ import annotations

import random
import unittest

import generators
import support
from seismic_phase_associator.pipeline import run


def _permutations(rng, items, count=5):
    seen = set()
    out = []
    for _ in range(count):
        shuffled = list(items)
        rng.shuffle(shuffled)
        key = repr(shuffled)
        if key in seen:
            continue
        seen.add(key)
        out.append(shuffled)
    return out


class TestPermutationInvariance(unittest.TestCase):
    def test_reordering_input_never_changes_events(self):
        for name, generator in generators.all_generators().items():
            for seed in range(40):
                data = generator(random.Random(seed))
                base = run(data)
                reference = support.canonical(base)
                for picks in _permutations(random.Random(seed + 900), data["picks"]):
                    variant = dict(data, picks=picks)
                    got = support.canonical(run(variant))
                    self.assertEqual(
                        got,
                        reference,
                        "%s seed %d: permuting input changed the association\n%r\n%r"
                        % (name, seed, data, variant),
                    )

    def test_reordering_input_never_changes_rejections(self):
        data = support.payload(
            [
                support.pick("A", "P", 1.0),
                support.pick("A", "P", 1.0),
                support.pick("C", "P", 9.0),
                {"station": "B", "phase": "Q", "time": 2.0},
                support.pick("D", "P", 1.1),
            ]
        )
        base = run(data)
        for picks in _permutations(random.Random(7), data["picks"]):
            got = run(dict(data, picks=picks))
            self.assertEqual(
                [e["event_id"] for e in got["events"]],
                [e["event_id"] for e in base["events"]],
            )
            self.assertEqual(
                sorted(r["reason"] for r in got["rejected_picks"]),
                sorted(r["reason"] for r in base["rejected_picks"]),
            )
            self.assertEqual(got["summary"], base["summary"])

    def test_event_ids_are_assigned_after_sorting(self):
        early = support.payload(
            [
                support.pick("B", "P", 1.1),
                support.pick("A", "P", 1.0),
                support.pick("D", "P", 5.1),
                support.pick("C", "P", 5.0),
            ]
        )
        late_first = dict(early, picks=list(reversed(early["picks"])))
        left = run(early)
        right = run(late_first)
        self.assertEqual([e["origin_time"] for e in left["events"]], [1.0, 5.0])
        # Source indices necessarily follow the permutation; association,
        # IDs and pick order inside each event must not.
        self.assertEqual(support.events_only(left), support.events_only(right))


class TestDuplicateDelivery(unittest.TestCase):
    def test_repeating_a_pick_does_not_change_events(self):
        for seed in range(40):
            data = generators.normal_payload(random.Random(seed))
            if not data["picks"]:
                continue
            base = run(data)
            repeated = dict(data, picks=data["picks"] + data["picks"] + data["picks"])
            got = run(repeated)
            self.assertEqual(
                support.events_only(got),
                support.events_only(base),
                "triple delivery changed events for seed %d: %r" % (seed, data),
            )
            self.assertEqual(
                got["summary"]["accepted_pick_count"],
                base["summary"]["accepted_pick_count"],
            )
            # Every extra delivery is accounted for exactly once: the unique
            # arrival count is unchanged and all surplus copies are rejected as
            # duplicates (input - unique), never double-counted as stations.
            self.assertEqual(
                got["summary"]["unique_pick_count"],
                base["summary"]["unique_pick_count"],
            )
            self.assertEqual(
                got["summary"]["duplicate_pick_count"],
                got["summary"]["input_pick_count"]
                - got["summary"]["unique_pick_count"],
            )

    def test_duplicate_stations_are_not_counted_twice(self):
        # A repeated station must not turn a one-station event into accepted.
        data = support.payload(
            [
                support.pick("A", "P", 1.0),
                support.pick("A", "P", 1.0),
                support.pick("A", "P", 1.0),
            ]
        )
        result = run(data)
        self.assertEqual(result["events"], [])
        self.assertEqual(
            [item["reason"] for item in result["rejected_picks"]],
            ["duplicate_pick", "duplicate_pick", "insufficient_stations"],
        )

    def test_each_duplicate_reports_its_raw_pick(self):
        original = support.pick("A", "P", 1.0, label="first")
        repeat = support.pick("A", "P", 1.0, label="again")
        data = support.payload([original, repeat, support.pick("B", "P", 1.1)])
        result = run(data)
        duplicates = [
            item for item in result["rejected_picks"]
            if item["reason"] == "duplicate_pick"
        ]
        self.assertEqual(len(duplicates), 1)
        # The representative is canonical ("again" < "first"), not
        # first-arrival; what matters is that the other copy is reported
        # verbatim and no copy is silently lost.
        self.assertIn(duplicates[0]["raw_pick"], (original, repeat))
        # The surviving copy is chosen canonically (independent of arrival
        # order) and records the delivery count and the displaced raw pick.
        survivor = [
            pick for pick in result["events"][0]["picks"] if pick["station"] == "A"
        ][0]
        self.assertEqual(survivor["delivery_count"], 2)
        self.assertEqual(
            sorted(
                [survivor["raw_pick"], duplicates[0]["raw_pick"]],
                key=repr,
            ),
            sorted([original, repeat], key=repr),
        )
        self.assertNotEqual(survivor["raw_pick"], duplicates[0]["raw_pick"])
        self.assertEqual(
            survivor["duplicate_source_indices"],
            [duplicates[0]["source_index"]],
        )

    def test_duplicate_representative_is_arrival_order_independent(self):
        first = support.pick("A", "P", 1.0, label="aaa")
        second = support.pick("A", "P", 1.0, label="zzz")
        third = support.pick("B", "P", 1.1)
        left = run(support.payload([first, second, third]))
        right = run(support.payload([second, first, third]))
        pick_left = [p for p in left["events"][0]["picks"] if p["station"] == "A"][0]
        pick_right = [p for p in right["events"][0]["picks"] if p["station"] == "A"][0]
        self.assertEqual(pick_left["raw_pick"], pick_right["raw_pick"])
        self.assertEqual(
            sorted(item["raw_pick"]["label"] for item in left["rejected_picks"]),
            sorted(item["raw_pick"]["label"] for item in right["rejected_picks"]),
        )


class TestBatching(unittest.TestCase):
    def test_time_disjoint_batches_equal_union_of_results(self):
        """Batches separated by more than the window cannot interact.

        Splitting a corpus at a wide temporal gap must give exactly the same
        events and rejections as running the two halves separately, modulo
        event renumbering.  This is the batching invariant: no hidden state
        may leak across the boundary.
        """

        for seed in range(30):
            data = generators.normal_payload(random.Random(seed), max_picks=8)
            if len(data["picks"]) < 2:
                continue
            # Push the second half far into the future so no window can bridge.
            picks = list(data["picks"])
            rng = random.Random(seed + 5)
            cut = rng.randrange(1, len(picks))
            shifted = [
                dict(pick, time=round(pick["time"] + 1000.0, 12))
                for pick in picks[cut:]
            ]
            whole_data = dict(data, picks=picks[:cut] + shifted)
            left_data = dict(data, picks=picks[:cut])
            right_data = dict(data, picks=shifted)

            whole = run(whole_data)
            left = run(left_data)
            right = run(right_data)

            combined_events = support.events_only(left) + support.events_only(right)
            # IDs are positional, so re-derive them for the combined view.
            renumbered = []
            for index, event in enumerate(
                sorted(
                    combined_events,
                    key=lambda e: (
                        e["origin_time"],
                        tuple(e["stations"]),
                        tuple(e["picks"]),
                    ),
                ),
                start=1,
            ):
                item = dict(event)
                item["event_id"] = "evt-%03d" % index
                renumbered.append(item)
            self.assertEqual(
                support.events_only(whole),
                renumbered,
                "time-disjoint batching changed events for seed %d" % seed,
            )
            self.assertEqual(
                support.rejection_view(whole),
                sorted(
                    support.rejection_view(left) + support.rejection_view(right)
                ),
                "time-disjoint batching changed rejections for seed %d" % seed,
            )
            self.assertEqual(
                whole["summary"]["accepted_pick_count"],
                left["summary"]["accepted_pick_count"]
                + right["summary"]["accepted_pick_count"],
            )

    def test_empty_batch_is_a_no_op(self):
        for seed in range(20):
            data = generators.normal_payload(random.Random(seed))
            base = run(data)
            with_empty = dict(data, picks=list(data["picks"]) + [])
            self.assertEqual(run(with_empty), base)
        empty = run({"picks": []})
        self.assertEqual(empty["events"], [])
        self.assertEqual(empty["rejected_picks"], [])
        self.assertEqual(empty["summary"]["input_pick_count"], 0)
        self.assertEqual(run({})["summary"]["input_pick_count"], 0)
        self.assertEqual(run({"picks": None})["events"], [])


class TestAddingAnIsolatedStation(unittest.TestCase):
    def test_unassociable_pick_never_joins_an_existing_event(self):
        base = support.payload(
            [support.pick("A", "P", 1.0), support.pick("B", "P", 1.1)]
        )
        before = run(base)
        for extra_time in (0.0, -2.0, 3.0):
            extra = support.pick("Z", "P", extra_time)
            after = run(dict(base, picks=base["picks"] + [extra]))
            self.assertEqual(
                support.events_only(before),
                support.events_only(after),
                "isolated pick at %r changed the accepted event" % extra_time,
            )
            self.assertEqual(
                [item["reason"] for item in after["rejected_picks"]],
                ["insufficient_stations"],
            )
            self.assertEqual(
                after["rejected_picks"][0]["raw_pick"], extra
            )


class TestDifferentialOnPermutations(unittest.TestCase):
    def test_oracle_agrees_under_permutation(self):
        for seed in range(60):
            data = generators.messy_payload(random.Random(seed))
            for picks in _permutations(random.Random(seed + 31), data["picks"]):
                variant = dict(data, picks=picks)
                support.assert_matches_oracle(self, variant)


if __name__ == "__main__":
    unittest.main()
