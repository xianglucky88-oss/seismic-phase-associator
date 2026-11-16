import argparse
import json
from pathlib import Path
from .pipeline import run

def main() -> None:
    parser = argparse.ArgumentParser(description='Associate station phase picks into versioned earthquake events.')
    parser.add_argument("input", nargs="?", default=str(Path(__file__).parents[2] / "fixtures" / "input.json"))
    args = parser.parse_args()
    payload = json.loads(Path(args.input).read_text())
    print(json.dumps(run(payload), ensure_ascii=False, indent=2, sort_keys=True))

if __name__ == "__main__":
    main()
