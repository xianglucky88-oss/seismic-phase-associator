"""Compute the run_key for a payload, as specified in docs/CATALOG_REGISTER.md.

run_key = sha256 of a canonical JSON serialisation of the INPUT payload
(keys sorted, compact separators). It identifies the input only; it says nothing
about the code that processed it, so a code_ref must always be recorded
alongside it.

Usage:
    python3 tools/run_key.py fixtures/input.json
"""
import hashlib
import json
import sys


def run_key(payload):
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()


def main():
    if len(sys.argv) != 2:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    try:
        with open(sys.argv[1], encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"cannot read payload: {exc}", file=sys.stderr)
        return 2
    print(run_key(payload))
    return 0


if __name__ == "__main__":
    sys.exit(main())
