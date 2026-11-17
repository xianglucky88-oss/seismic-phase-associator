import argparse
import json
from pathlib import Path

from .engine import DEFAULT_WINDOW
from .pipeline import run


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(
        description="Associate station phase picks into versioned earthquake events."
    )
    parser.add_argument(
        "input",
        nargs="?",
        default=str(Path(__file__).parents[2] / "fixtures" / "input.json"),
    )
    parser.add_argument(
        "--window",
        type=float,
        default=DEFAULT_WINDOW,
        help="inclusive association window in seconds (default: %(default)s)",
    )
    args = parser.parse_args(argv)

    payload = json.loads(Path(args.input).read_text())
    result = run(payload, window=args.window)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
