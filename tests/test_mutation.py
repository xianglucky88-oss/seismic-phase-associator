"""Tests for the mutation harness itself.

A mutation score is only meaningful if the harness can actually distinguish a
live mutant from the pristine tree.  These tests verify:

* the control run passes,
* representative mutants are killed by the differential probe,
* the surviving set is empty for the curated mutant catalogue, and
* the probe is the thing doing the killing (not an import error).
"""

from __future__ import annotations

import unittest

import mutation_runner


class TestMutationHarness(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        healthy, output = mutation_runner.run_control()
        cls.control_healthy = healthy
        cls.control_output = output

    def test_control_passes(self):
        self.assertTrue(
            self.control_healthy,
            "unmutated tree fails the probe:\n%s" % self.control_output,
        )

    def test_a_known_behavioural_mutant_is_killed(self):
        mutant = next(
            m for m in mutation_runner.MUTANTS if m.name == "correction-ignored"
        )
        killed, output = mutation_runner.run_mutant(mutant)
        self.assertTrue(killed, output)
        self.assertIn("diff", output.lower())

    def test_report_mutants_are_killed(self):
        for name in (
            "report-events-unsorted",
            "report-pick-count-stale",
            "report-drops-raw-picks",
        ):
            mutant = next(m for m in mutation_runner.MUTANTS if m.name == name)
            killed, output = mutation_runner.run_mutant(mutant)
            self.assertTrue(killed, "%s survived: %s" % (name, output))

    def test_anchor_drift_is_reported_not_silently_skipped(self):
        broken = mutation_runner.Mutant(
            "anchor-drift",
            "engine.py",
            "this text does not exist",
            "replacement",
            "anchor that will not match",
        )
        with self.assertRaises(RuntimeError):
            mutation_runner.run_mutant(broken)

    def test_full_catalogue_has_no_survivors(self):
        survivors = []
        for mutant in mutation_runner.MUTANTS:
            killed, output = mutation_runner.run_mutant(mutant)
            if not killed:
                survivors.append((mutant.name, output))
        self.assertEqual(survivors, [], "surviving mutants: %r" % (survivors,))


if __name__ == "__main__":
    unittest.main()
