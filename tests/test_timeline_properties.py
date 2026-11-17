"""Timeline property tests.

These are algebraic properties over the time axis rather than golden outputs:

* global time translation is equivariant (no hidden absolute epoch)
* a per-station clock correction is exactly a per-station translation
* the correction is idempotent in the sense that it is applied once
* monotone time scaling preserves association structure
* the window is a threshold on *corrected* time only

Every failure message includes the payload and the raw picks involved.
"""

from __future__ import annotations

import json
import random
import unittest

import generators
import support
from seismic_phase_associator.pipeline import run


def _shift_payload(data, shift, stations=None):
    shifted = {
        "picks": [
            dict(item, time=round(item["time"] + shift, 12))
            if isinstance(item, dict) and isinstance(item.get("time"), (int, float))
            else item
            for item in data.get("picks", [])
        ]
    }
    if "clock_corrections" in data:
        shifted["clock_corrections"] = dict(data["clock_corrections"])
    return shifted


def _shift_result(result, shift):
    """Shift every reported time by ``shift`` (origin + corrected times)."""

    def move(pick):
        item = dict(pick)
        item["time"] = round(item["time"] + shift, 12) if isinstance(item["time"], (int, float)) else item["time"]
        item["corrected_time"] = round(item["corrected_time"] + shift, 12)
        if isinstance(item.get("raw_pick"), dict) and isinstance(
            item["raw_pick"].get("time"), (int, float)
        ):
            item["raw_pick"] = dict(
                item["raw_pick"], time=round(item["raw_pick"]["time"] + shift, 12)
            )
        return item

    events = []
    for event in result["events"]:
        moved = dict(event)
        moved["origin_time"] = round(event["origin_time"] + shift, 12)
        moved["picks"] = [move(p) for p in event["picks"]]
        events.append(moved)

    rejected = []
    for item in result["rejected_picks"]:
        moved = dict(item)
        if isinstance(moved.get("time"), (int, float)):
            moved["time"] = round(moved["time"] + shift, 12)
        if isinstance(moved.get("corrected_time"), (int, float)):
            moved["corrected_time"] = round(moved["corrected_time"] + shift, 12)
        if isinstance(moved.get("raw_pick"), dict) and isinstance(
            moved["raw_pick"].get("time"), (int, float)
        ):
            moved["raw_pick"] = dict(
                moved["raw_pick"], time=round(moved["raw_pick"]["time"] + shift, 12)
            )
        rejected.append(moved)

    out = dict(result)
    out["events"] = events
    out["rejected_picks"] = rejected
    return out


class TestTimelineProperties(unittest.TestCase):
    def test_global_translation_is_equivariant(self):
        for seed in range(60):
            data = generators.normal_payload(random.Random(seed))
            if not data["picks"]:
                continue
            shift = random.Random(seed + 1000).choice([-100.0, 0.5, 7.25, 1000.0])
            base = run(data)
            moved = run(_shift_payload(data, shift))
            expected = _shift_result(base, shift)
            # Association structure and every time must translate.  Rejection
            # *order* is pinned by a raw-content tie-break, which legitimately
            # depends on the absolute time string, so compare it as a multiset.
            self.assertEqual(
                support.events_only(expected),
                support.events_only(moved),
                "seed %d shift %r changed events" % (seed, shift),
            )

            def rejections(result):
                return sorted(
                    (
                        item["reason"],
                        item.get("station"),
                        item.get("phase"),
                        item.get("time"),
                        item.get("corrected_time"),
                        json.dumps(item.get("raw_pick"), sort_keys=True, default=repr),
                    )
                    for item in result["rejected_picks"]
                )

            self.assertEqual(
                rejections(expected),
                rejections(moved),
                "seed %d shift %r changed rejections" % (seed, shift),
            )
            self.assertEqual(expected["summary"], moved["summary"])

    def test_clock_correction_is_a_per_station_translation(self):
        for seed in range(60):
            data = generators.normal_payload(random.Random(seed), corrections=False)
            if not data["picks"]:
                continue
            rng = random.Random(seed + 77)
            corrections = {
                station: rng.choice([-0.25, -0.05, 0.05, 0.25])
                for station in support.STATIONS
                if rng.random() < 0.6
            }
            corrected = dict(data, clock_corrections=corrections)
            manually_moved = {
                "picks": [
                    dict(
                        pick,
                        time=round(pick["time"] + corrections.get(pick["station"], 0.0), 12),
                    )
                    for pick in data["picks"]
                ]
            }
            left = run(corrected)
            right = run(manually_moved)
            # Raw picks differ by design, so compare association structure.
            self.assertEqual(
                support.association_view(left),
                support.association_view(right),
                "correction was not equivalent to a per-station translation\n%r\n%r"
                % (support.association_view(left), support.association_view(right)),
            )
            self.assertEqual(
                support.rejection_view(left), support.rejection_view(right)
            )

    def test_correction_is_applied_exactly_once(self):
        data = support.payload(
            [support.pick("A", "P", 1.0), support.pick("B", "P", 1.3)],
            {"A": 0.1},
        )
        once = run(data)
        self.assertEqual(once["events"][0]["picks"][0]["corrected_time"], 1.1)
        self.assertEqual(once["events"][0]["picks"][0]["clock_correction"], 0.1)
        # Applying the correction exactly once is observationally identical to
        # translating only that station's picks before association.
        translated = run(
            support.payload([support.pick("A", "P", 1.1), support.pick("B", "P", 1.3)])
        )
        self.assertEqual(support.association_view(once), support.association_view(translated))
        self.assertEqual(once["events"][0]["origin_time"], 1.1)

    def test_window_uses_corrected_time_only(self):
        # Raw gap is 0.3 (> 0.25); a -0.05 correction is not enough.
        far = support.payload(
            [support.pick("A", "P", 1.0), support.pick("B", "P", 1.31)],
            {"A": 0.05},
        )
        self.assertEqual(len(run(far)["events"]), 0)
        # Raw gap is 0.31; B moved -0.05, A moved +0.05 -> corrected gap 0.21.
        near = support.payload(
            [support.pick("A", "P", 1.0), support.pick("B", "P", 1.31)],
            {"A": 0.05, "B": -0.05},
        )
        result = run(near)
        self.assertEqual(len(result["events"]), 1)
        self.assertEqual(result["events"][0]["origin_time"], 1.05)

    def test_monotone_time_scaling_preserves_groups(self):
        for seed in range(40):
            data = generators.normal_payload(random.Random(seed), corrections=False)
            if not data["picks"]:
                continue
            scale = 2.0
            scaled = {
                "picks": [
                    dict(pick, time=round(pick["time"] * scale, 12))
                    for pick in data["picks"]
                ]
            }
            # Doubling time while doubling the window keeps the same grouping.
            left = run(data)
            right = run(scaled, window=0.5)
            self.assertEqual(
                [[p["station"] for p in e["picks"]] for e in left["events"]],
                [[p["station"] for p in e["picks"]] for e in right["events"]],
                "scaling changed grouping for seed %d: %r" % (seed, data),
            )


class TestWindowBoundaryProperties(unittest.TestCase):
    def test_boundary_is_inclusive_from_both_sides(self):
        for delta, expected in [(0.25, 1), (0.2500001, 0), (-0.25, 1), (-0.2500001, 0)]:
            data = support.payload(
                [support.pick("A", "P", 1.0), support.pick("B", "P", 1.0 + delta)]
            )
            self.assertEqual(
                len(run(data)["events"]), expected, "delta=%r" % delta
            )

    def test_boundary_after_correction(self):
        data = support.payload(
            [support.pick("A", "P", 1.0), support.pick("B", "P", 1.2)],
            {"B": 0.05},
        )
        self.assertEqual(len(run(data)["events"]), 1)
        data["clock_corrections"]["B"] = 0.06
        self.assertEqual(len(run(data)["events"]), 0)

    def test_zero_window_requires_identical_times(self):
        same = support.payload(
            [support.pick("A", "P", 1.0), support.pick("B", "P", 1.0)]
        )
        self.assertEqual(len(run(same, window=0.0)["events"]), 1)
        apart = support.payload(
            [support.pick("A", "P", 1.0), support.pick("B", "P", 1.0000001)]
        )
        self.assertEqual(len(run(apart, window=0.0)["events"]), 0)

    def test_negative_window_and_bad_payloads_are_rejected(self):
        with self.assertRaises(ValueError):
            run(support.payload([]), window=-1)
        with self.assertRaises(ValueError):
            run(support.payload([]), window=True)
        with self.assertRaises(ValueError):
            run(support.payload([]), window="0.25")


if __name__ == "__main__":
    unittest.main()
