"""Differential replay for change-impact review.

Runs the *existing* pipeline on two payloads and reports what changed. This is
review tooling: it adds no association logic and imports the same `run()` the
CLI uses.

Because `event_id` is positional and not a stable identity (see
docs/DATA_CONTRACT.md), events are matched here by station-set overlap, and an
identifier change is reported as its own category rather than being hidden.

Usage:
    PYTHONPATH=src python3 tools/replay_diff.py OLD.json NEW.json
    PYTHONPATH=src python3 tools/replay_diff.py OLD.json NEW.json --strict
    PYTHONPATH=src python3 tools/replay_diff.py OLD.json OLD.json \
        --new-corrections changed_clock.json      # station-config review

Exit codes:
    0  no behavioural difference
    1  differences found (or --strict and any difference)
    2  a payload failed to load or run
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from seismic_phase_associator.pipeline import run  # noqa: E402


def load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def execute(payload, label):
    try:
        result = run(payload)
    except Exception as exc:  # the contract documents these as possible
        return None, f"{label}: {type(exc).__name__}: {exc}"
    return result, None


def overlap(a, b):
    sa, sb = set(a), set(b)
    union = sa | sb
    return len(sa & sb) / len(union) if union else 0.0


def match_events(old, new):
    """Greedy best-overlap matching. Returns list of (old_ev, new_ev_or_None)."""
    # Candidates scored by station Jaccard, then by proximity of origin_time.
    candidates = []
    for i, o in enumerate(old):
        for j, n in enumerate(new):
            score = overlap(o["stations"], n["stations"])
            if score > 0:
                dt = abs(o["origin_time"] - n["origin_time"])
                # Prefer the higher station overlap, then a preserved
                # identifier, then the closer origin_time. Without the
                # identifier tie-break, two physically identical duplicates
                # would be reported as a spurious id change.
                same_id = 1 if o["event_id"] == n["event_id"] else 0
                candidates.append((score, same_id, -dt, i, j))
    candidates.sort(reverse=True)
    used_old, used_new, pairs = set(), set(), {}
    for _score, _same, _negdt, i, j in candidates:
        if i in used_old or j in used_new:
            continue
        used_old.add(i)
        used_new.add(j)
        pairs[i] = j
    ordered = [(old[i], new[pairs[i]] if i in pairs else None) for i in range(len(old))]
    added = [n for j, n in enumerate(new) if j not in used_new]
    return ordered, added


def diff(old_result, new_result):
    old_events = old_result["events"]
    new_events = new_result["events"]
    pairs, added = match_events(old_events, new_events)
    changes = []
    for old_ev, new_ev in pairs:
        if new_ev is None:
            changes.append({
                "kind": "event_removed",
                "event_id": old_ev["event_id"],
                "origin_time": old_ev["origin_time"],
                "stations": old_ev["stations"],
            })
            continue
        fields = {}
        if old_ev["event_id"] != new_ev["event_id"]:
            fields["event_id"] = [old_ev["event_id"], new_ev["event_id"]]
        if old_ev["origin_time"] != new_ev["origin_time"]:
            fields["origin_time"] = [old_ev["origin_time"], new_ev["origin_time"]]
        if old_ev["stations"] != new_ev["stations"]:
            fields["stations"] = [old_ev["stations"], new_ev["stations"]]
        if old_ev["pick_count"] != new_ev["pick_count"]:
            fields["pick_count"] = [old_ev["pick_count"], new_ev["pick_count"]]
        if fields:
            changes.append({
                "kind": "event_changed",
                "matched_by": sorted(set(old_ev["stations"]) & set(new_ev["stations"])),
                "event_id": [old_ev["event_id"], new_ev["event_id"]],
                "changes": fields,
            })
    for new_ev in added:
        changes.append({
            "kind": "event_added",
            "event_id": new_ev["event_id"],
            "origin_time": new_ev["origin_time"],
            "stations": new_ev["stations"],
        })
    if old_result["rejected_pick_count"] != new_result["rejected_pick_count"]:
        changes.append({
            "kind": "rejected_pick_count",
            "changes": [old_result["rejected_pick_count"],
                        new_result["rejected_pick_count"]],
        })
    return changes


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("old")
    ap.add_argument("new")
    ap.add_argument("--new-corrections",
                    help="replace clock_corrections in NEW with this JSON file "
                         "(station-configuration review)")
    ap.add_argument("--strict", action="store_true",
                    help="exit 1 on any difference")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    try:
        old_payload = load(args.old)
        new_payload = load(args.new)
        if args.new_corrections:
            new_payload = dict(new_payload)
            new_payload["clock_corrections"] = load(args.new_corrections)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"cannot read payload: {exc}", file=sys.stderr)
        return 2

    old_result, old_err = execute(old_payload, args.old)
    new_result, new_err = execute(new_payload, args.new)
    if old_err or new_err:
        for err in (old_err, new_err):
            if err:
                print(err, file=sys.stderr)
        return 2

    changes = diff(old_result, new_result)
    summary = {
        "old_events": len(old_result["events"]),
        "new_events": len(new_result["events"]),
        "old_rejected": old_result["rejected_pick_count"],
        "new_rejected": new_result["rejected_pick_count"],
        "changed": bool(changes),
        "changes": changes,
    }
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"events {summary['old_events']} -> {summary['new_events']}; "
              f"rejected {summary['old_rejected']} -> {summary['new_rejected']}")
        if not changes:
            print("no behavioural difference")
        for ch in changes:
            print(f"  - {ch['kind']}: "
                  + json.dumps({k: v for k, v in ch.items() if k != 'kind'},
                               sort_keys=True))
    if changes or args.strict:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
