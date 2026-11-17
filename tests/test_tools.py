"""Tests for the change-impact replay tool.

These validate the review tooling described in docs/REVIEW_AND_ROLLBACK.md.
They deliberately do not assert on new association behaviour -- only that the
tool reports the differences the handbook promises it reports.
"""
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

import replay_diff  # noqa: E402
import run_key  # noqa: E402
from seismic_phase_associator.pipeline import run  # noqa: E402

SCENARIOS = ROOT / "fixtures" / "scenarios"


def payload(scenario, name):
    return json.loads((SCENARIOS / scenario / f"{name}.json").read_text())


def kinds(changes):
    return sorted(c["kind"] for c in changes)


class TestReplayDiff(unittest.TestCase):
    def test_identical_inputs_report_nothing(self):
        p = payload("late_pick", "before")
        self.assertEqual(replay_diff.diff(run(p), run(p)), [])

    def test_late_pick_reports_origin_and_membership_change(self):
        changes = replay_diff.diff(
            run(payload("late_pick", "before")),
            run(payload("late_pick", "after")),
        )
        self.assertIn("event_changed", kinds(changes))
        changed = next(c for c in changes if c["kind"] == "event_changed")
        self.assertEqual(changed["changes"]["origin_time"], [10.0, 9.8])
        self.assertIn("stations", changed["changes"])

    def test_retraction_collapse_reports_removal(self):
        changes = replay_diff.diff(
            run(payload("retraction", "before")),
            run(payload("retraction", "collapse")),
        )
        self.assertIn("event_removed", kinds(changes))

    def test_merge_reports_added_event(self):
        changes = replay_diff.diff(
            run(payload("merge", "before_a")),
            run(payload("merge", "merged")),
        )
        self.assertIn("event_added", kinds(changes))

    def test_duplicate_phantom_is_an_added_event(self):
        changes = replay_diff.diff(
            run(payload("duplicate", "replay")),
            run(payload("duplicate", "phantom_event")),
        )
        self.assertIn("event_added", kinds(changes))

    def test_rejected_count_change_is_reported(self):
        changes = replay_diff.diff(
            run(payload("clock_correction", "before")),
            run(payload("clock_correction", "split_event")),
        )
        self.assertIn("rejected_pick_count", kinds(changes))

    def test_cli_exit_codes(self):
        base = [sys.executable, str(ROOT / "tools" / "replay_diff.py")]
        env = {"PYTHONPATH": str(ROOT / "src")}
        import os
        env = {**os.environ, **env}

        same = subprocess.run(
            base + [str(SCENARIOS / "late_pick" / "before.json"),
                    str(SCENARIOS / "late_pick" / "before.json")],
            capture_output=True, env=env,
        )
        self.assertEqual(same.returncode, 0, same.stderr)

        diff = subprocess.run(
            base + [str(SCENARIOS / "late_pick" / "before.json"),
                    str(SCENARIOS / "late_pick" / "after.json")],
            capture_output=True, env=env,
        )
        self.assertEqual(diff.returncode, 1, diff.stderr)

        missing = subprocess.run(
            base + [str(ROOT / "fixtures" / "does-not-exist.json"),
                    str(SCENARIOS / "late_pick" / "before.json")],
            capture_output=True, env=env,
        )
        self.assertEqual(missing.returncode, 2, missing.stderr)


class TestRunKey(unittest.TestCase):
    def test_key_is_deterministic(self):
        p = payload("late_pick", "before")
        self.assertEqual(run_key.run_key(p), run_key.run_key(p))

    def test_key_ignores_object_key_order(self):
        a = {"picks": [{"time": 10.0, "station": "A", "phase": "P"}]}
        b = {"picks": [{"station": "A", "phase": "P", "time": 10.0}]}
        self.assertEqual(run_key.run_key(a), run_key.run_key(b))

    def test_key_changes_with_input(self):
        self.assertNotEqual(
            run_key.run_key(payload("late_pick", "before")),
            run_key.run_key(payload("late_pick", "after")),
        )

    def test_cli_reports_missing_file(self):
        proc = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "run_key.py"), "/nope.json"],
            capture_output=True,
        )
        self.assertEqual(proc.returncode, 2)


class TestDocsAreInSync(unittest.TestCase):
    """The scenario table quoted in SCENARIOS.md must match live output."""

    def test_scenario_table_matches_document(self):
        import re

        import scenario_report

        doc = (ROOT / "docs" / "SCENARIOS.md").read_text()
        match = re.search(r"```\n(late_pick/before.*?)```", doc, re.S)
        self.assertIsNotNone(match, "scenario table block not found in docs")
        documented = [
            re.sub(r"\s+", " ", line.strip())
            for line in match.group(1).strip().splitlines()
        ]

        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with redirect_stdout(buf):
            scenario_report.main([])
        live = [
            re.sub(r"\s+", " ", line.strip())
            for line in buf.getvalue().strip().splitlines()
        ]

        self.assertEqual(documented, live,
                         "SCENARIOS.md table drifted from tools/scenario_report.py")


if __name__ == "__main__":
    unittest.main()
