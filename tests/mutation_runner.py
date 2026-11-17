"""Minimal mutation-testing harness for the associator.

It writes single-point mutants of ``src/seismic_phase_associator`` into a
temporary tree, runs the differential probe against each mutant, and reports
which mutants survive.  No third-party tooling is required.

Usage::

    python3 tests/mutation_runner.py            # run the built-in mutant set
    python3 tests/mutation_runner.py --json     # machine-readable summary
    python3 tests/mutation_runner.py --list     # list mutant names

The mutant set targets the failure classes called out by the task: clock
correction scope, window boundary, grouping/slot occupancy, report ordering,
deduplication and station counting.  A surviving mutant is a real test gap,
not a flaky result: the probe corpus is seeded.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import sys
from pathlib import Path

# Allow ``python3 -m unittest discover -s tests`` to work from the repo root
# without an externally-set PYTHONPATH.
_SRC = Path(__file__).resolve().parents[1] / "src"
# Append, not insert: when a mutant tree is first on sys.path it must win.
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.append(str(_SRC))

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
TESTS = ROOT / "tests"
PACKAGE = "seismic_phase_associator"

# With ``-I`` Python ignores ``PYTHONPATH`` and does not add the script
# directory, so the probe gets its two roots explicitly.  Anything printed or
# returned by the probe is treated as evidence the mutant changed behaviour.
BOOTSTRAP = """\
import runpy, sys
sys.path[:0] = [sys.argv[1], sys.argv[2]]
runpy.run_path(sys.argv[2] + '/differential_probe.py', run_name='__main__')
"""


@dataclass(frozen=True)
class Mutant:
    name: str
    module: str
    old: str
    new: str
    description: str


MUTANTS = [
    Mutant(
        "correction-applies-to-all-stations",
        "engine.py",
        'def effective_time(record: Mapping[str, Any], corrections: Mapping[str, float]) -> float:\n    return float(record["time"]) + float(corrections.get(record["station"], 0.0))',
        'def effective_time(record: Mapping[str, Any], corrections: Mapping[str, float]) -> float:\n    return float(record["time"]) + float(corrections.get("STA01", 0.0))',
        "clock correction leaked to every station",
    ),
    Mutant(
        "correction-applied-twice",
        "engine.py",
        'return float(record["time"]) + float(corrections.get(record["station"], 0.0))',
        'return float(record["time"]) + 2.0 * float(corrections.get(record["station"], 0.0))',
        "clock correction double applied",
    ),
    Mutant(
        "correction-ignored",
        "engine.py",
        'return float(record["time"]) + float(corrections.get(record["station"], 0.0))',
        'return float(record["time"])',
        "clock correction dropped entirely",
    ),
    Mutant(
        "window-boundary-exclusive",
        "engine.py",
        "if abs(pick_time - event[\"origin_time\"]) <= window + BOUNDARY_EPSILON",
        "if abs(pick_time - event[\"origin_time\"]) < window - BOUNDARY_EPSILON",
        "inclusive window boundary made exclusive",
    ),
    Mutant(
        "window-ignores-corrections",
        "engine.py",
        'pick_time = effective_time(record, corrections)',
        'pick_time = float(record["time"])',
        "association window uses raw instead of corrected time",
    ),
    Mutant(
        "latest-anchor-instead-of-earliest",
        "engine.py",
        'match = min(candidates, key=lambda e: e["origin_time"]) if candidates else None',
        'match = max(candidates, key=lambda e: e["origin_time"]) if candidates else None',
        "event anchoring drifts to the latest candidate",
    ),
    Mutant(
        "duplicates-not-collapsed",
        "engine.py",
        "groups[_dedup_key(record)].append(record)",
        "groups[id(record)].append(record)",
        "duplicate delivery counted twice",
    ),
    Mutant(
        "duplicate-ignores-phase",
        "engine.py",
        'return (record["station"], record["phase"], float(record["time"]))',
        'return (record["station"], float(record["time"]))',
        "dedup key drops phase",
    ),
    Mutant(
        "slot-ignores-phase",
        "engine.py",
        'and (record["station"], record["phase"]) not in event["_occupancy"]',
        'and record["station"] not in {s for s, _p in event["_occupancy"]}',
        "event occupancy ignores phase, P and S cannot coexist",
    ),
    Mutant(
        "single-station-events-accepted",
        "engine.py",
        "if len(stations) >= MIN_STATIONS_PER_EVENT:",
        "if len(stations) >= 1:",
        "singleton events accepted as real events",
    ),
    Mutant(
        "stations-counted-with-duplicates",
        "engine.py",
        "stations = {p[\"station\"] for p in event[\"_members\"]}",
        "stations = [p[\"station\"] for p in event[\"_members\"]]",
        "duplicate stations counted as distinct",
    ),
    Mutant(
        "unparsable-picks-dropped-silently",
        "parser.py",
        'return None, _rejection(index, "invalid_record", "pick must be an object", raw)',
        "return None, None",
        "invalid row disappears without a rejection reason",
    ),
    Mutant(
        "unsupported-phase-accepted",
        "parser.py",
        "if phase not in SUPPORTED_PHASES:",
        "if phase not in SUPPORTED_PHASES and False:",
        "unknown phase accepted into association",
    ),
    Mutant(
        "empty-station-accepted",
        "parser.py",
        "if not isinstance(station, str) or not station.strip():",
        "if not isinstance(station, str):",
        "blank station name accepted as a station",
    ),
    Mutant(
        "invalid-correction-not-diagnosed",
        "parser.py",
        "        if not _is_number(value) or not math.isfinite(float(value)):\n            diagnostics.append(",
        "        if False:\n            diagnostics.append(",
        "invalid clock correction silently becomes a crash or zero",
    ),
    Mutant(
        "report-picks-unsorted",
        "report.py",
        'item["picks"] = sorted(item.get("picks", []), key=_pick_key)',
        'item["picks"] = list(item.get("picks", []))',
        "reported pick order depends on input order",
    ),
    Mutant(
        "report-events-unsorted",
        "report.py",
        "for event in sorted(events, key=_event_key):",
        "for event in events:",
        "event order (and therefore IDs) depends on input order",
    ),
    Mutant(
        "report-pick-count-stale",
        "report.py",
        'item["pick_count"] = len(item["picks"])',
        'item["pick_count"] = item.get("pick_count", 0)',
        "pick_count not recomputed from the final pick list",
    ),
    Mutant(
        "report-drops-raw-picks",
        "report.py",
        'item = json_safe(event)',
        'item = {k: v for k, v in event.items() if k != "picks"}\n        item = json_safe(item)',
        "report omits picks and their raw payloads",
    ),
    Mutant(
        "pipeline-drops-parse-rejections",
        "pipeline.py",
        "all_rejected = parse_rejections + data[\"rejected_picks\"]",
        "all_rejected = data[\"rejected_picks\"]",
        "parser rejections vanish from the final report",
    ),
    Mutant(
        "pipeline-unsorted-rejections",
        "pipeline.py",
        "    all_rejected.sort(",
        "    all_rejected  # ",
        "rejection order depends on input order",
    ),
]


def _apply(module_path: Path, mutant: Mutant) -> None:
    text = module_path.read_text()
    if mutant.old not in text:
        raise RuntimeError(
            "mutant %r anchor not found in %s" % (mutant.name, module_path.name)
        )
    module_path.write_text(text.replace(mutant.old, mutant.new, 1))


def run_mutant(mutant: Mutant, timeout: int = 60) -> tuple[bool, str]:
    """Return ``(killed, output)`` for one mutant."""

    with tempfile.TemporaryDirectory(prefix="mutant-") as tmp:
        tree = Path(tmp) / "src"
        shutil.copytree(
            SRC, tree, ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
        )
        module_path = tree / PACKAGE / mutant.module
        _apply(module_path, mutant)

        env = {
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "PYTHONPATH": "",
        }
        try:
            completed = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-c",
                    BOOTSTRAP,
                    str(tree),
                    str(TESTS),
                ],
                cwd=tmp,
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return True, "timeout"
        output = (completed.stdout + completed.stderr).strip()
        # A clean baseline prints exactly the success line.  Anything else
        # (including a traceback, a diff report, or a different line count)
        # kills the mutant.
        if completed.returncode != 0:
            return True, output[:2000]
        if "ok: implementation matches reference oracle" not in output:
            return True, "probe did not report success: " + output[:2000]
        if output.count("\n") != 0:
            return True, "probe emitted unexpected output: " + output[:2000]
        return False, output[:2000]


def run_control(timeout: int = 60) -> tuple[bool, str]:
    """Run the probe against an unmodified copy.

    Returns ``(healthy, output)``.  If the control is not healthy every mutant
    result would be meaningless, so the harness refuses to report.
    """

    with tempfile.TemporaryDirectory(prefix="mutant-control-") as tmp:
        tree = Path(tmp) / "src"
        shutil.copytree(
            SRC, tree, ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
        )
        env = {"PATH": "/usr/bin:/bin:/usr/local/bin", "PYTHONPATH": ""}
        completed = subprocess.run(
            [sys.executable, "-I", "-c", BOOTSTRAP, str(tree), str(TESTS)],
            cwd=tmp,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = (completed.stdout + completed.stderr).strip()
        healthy = (
            completed.returncode == 0
            and "ok: implementation matches reference oracle" in output
        )
        return healthy, output[:2000]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--only", action="append", default=[])
    args = parser.parse_args(argv)

    selected = [m for m in MUTANTS if not args.only or m.name in args.only]

    if args.list:
        for mutant in selected:
            print("%-40s %s" % (mutant.name, mutant.description))
        return 0

    healthy, control_output = run_control()
    if not healthy:
        print("CONTROL FAILED: the unmutated tree does not pass the probe.")
        print(control_output)
        return 2

    results = []
    for mutant in selected:
        killed, output = run_mutant(mutant)
        results.append(
            {
                "name": mutant.name,
                "description": mutant.description,
                "killed": killed,
                "probe_output": output,
            }
        )
        if not args.json:
            print(
                "%-40s %s"
                % (mutant.name, "KILLED" if killed else "SURVIVED")
            )
            if not killed:
                print("  " + mutant.description)
                print("  " + output.replace("\n", "\n  "))

    survivors = [r for r in results if not r["killed"]]
    if args.json:
        print(
            json.dumps(
                {
                    "total": len(results),
                    "killed": len(results) - len(survivors),
                    "survived": len(survivors),
                    "results": results,
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        print(
            "\n%d/%d mutants killed (%d survived)"
            % (len(results) - len(survivors), len(results), len(survivors))
        )
    return 1 if survivors else 0


if __name__ == "__main__":
    sys.exit(main())
