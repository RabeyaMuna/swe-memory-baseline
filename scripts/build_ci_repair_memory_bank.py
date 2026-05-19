from __future__ import annotations

import argparse
from pathlib import Path

from sweagent.benchmark.ci_memory_plugin import build_hierarchical_memory_bank, summarize_hierarchical_memory_bank
from sweagent.benchmark.ci_repair_memory import load_rows, write_json
from sweagent.utils.config import load_environment_variables


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a lightweight CI repair memory bank from seed rows.")
    parser.add_argument("--seed-file", type=Path, required=True)
    parser.add_argument("--output-file", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    load_environment_variables()
    args = _parse_args()
    seed_rows = load_rows(args.seed_file)
    memory_bank = build_hierarchical_memory_bank(seed_rows)
    write_json(args.output_file, memory_bank)
    write_json(args.output_file.with_name("failure_memory.json"), memory_bank.get("l1") or [])
    write_json(args.output_file.with_name("repo_memory.json"), memory_bank.get("l2") or [])
    write_json(args.output_file.with_name("cross_memory.json"), memory_bank.get("l3") or [])
    write_json(
        args.output_file.with_name("memory_bank_summary.json"),
        summarize_hierarchical_memory_bank(
            memory_bank,
            seed_file=str(args.seed_file),
            analysis_file="",
            model_key="heuristic",
        ),
    )


if __name__ == "__main__":
    main()
