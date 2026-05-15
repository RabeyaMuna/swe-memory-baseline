from __future__ import annotations

import argparse
from pathlib import Path

from sweagent.benchmark.ci_repair_memory import build_memory_record, load_rows, write_json


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a lightweight CI repair memory bank from seed rows.")
    parser.add_argument("--seed-file", type=Path, required=True)
    parser.add_argument("--output-file", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    seed_rows = load_rows(args.seed_file)
    memory_bank = [build_memory_record(row) for row in seed_rows]
    write_json(args.output_file, memory_bank)


if __name__ == "__main__":
    main()

