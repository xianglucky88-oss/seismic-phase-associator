"""Regenerate the scenario table used by docs/DATA_CONTRACT.md.

This is a documentation-support tool, not part of the pipeline. It exists so the
"fixture-verifiable" column in the contract can be re-derived from the code
rather than trusted by hand.

Usage:
    PYTHONPATH=src python3 tools/scenario_report.py            # human table
    PYTHONPATH=src python3 tools/scenario_report.py --json      # machine data
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from seismic_phase_associator.pipeline import run  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ROOT / "fixtures" / "scenarios"

# Ordered so the document table is stable; (scenario, fixture) pairs.
CASES = [
    ("late_pick", "before"),
    ("late_pick", "after"),
    ("duplicate", "replay"),
    ("duplicate", "phantom_event"),
    ("retraction", "before"),
    ("retraction", "after"),
    ("retraction", "collapse"),
    ("merge", "before_a"),
    ("merge", "before_b"),
    ("merge", "merged"),
    ("merge", "shared_station"),
    ("clock_correction", "before"),
    ("clock_correction", "shift_origin"),
    ("clock_correction", "split_event"),
    ("association", "greedy_drop"),
    ("association", "greedy_shift"),
]


def summarise(payload):
    result = run(payload)
    return {
        "status": result["status"],
        "events": [
            {
                "event_id": e["event_id"],
                "origin_time": e["origin_time"],
                "stations": e["stations"],
                "pick_count": e["pick_count"],
            }
            for e in result["events"]
        ],
        "rejected_pick_count": result["rejected_pick_count"],
    }


def build():
    rows = []
    for scenario, name in CASES:
        path = SCENARIOS / scenario / f"{name}.json"
        payload = json.loads(path.read_text())
        rows.append(
            {
                "scenario": scenario,
                "fixture": str(path.relative_to(ROOT)),
                "summary": summarise(payload),
            }
        )
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    rows = build()
    if args.json:
        print(json.dumps(rows, indent=2, sort_keys=True))
        return
    for row in rows:
        s = row["summary"]
        evs = "; ".join(
            f"{e['event_id']}@{e['origin_time']}[{','.join(e['stations'])}]x{e['pick_count']}"
            for e in s["events"]
        ) or "(none)"
        rel = Path(row["fixture"]).relative_to(SCENARIOS.relative_to(ROOT))
        print(f"{str(rel):<36} rejected={s['rejected_pick_count']}  {evs}")


if __name__ == "__main__":
    main()
