from __future__ import annotations

import argparse
import json
from pathlib import Path

from sweagent.benchmark.ci_repair_memory import (
    build_expert_instance,
    load_rows,
    render_memory_context,
    retrieve_memory,
    write_json,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create SWE-agent expert instances, optionally with retrieved memory.")
    parser.add_argument("--eval-file", type=Path, required=True)
    parser.add_argument("--output-file", type=Path, required=True)
    parser.add_argument("--deployment-image", default="python:3.11")
    parser.add_argument("--memory-bank", type=Path)
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--min-score", type=float, default=0.15)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    eval_rows = load_rows(args.eval_file)
    memory_bank: list[dict] = []
    if args.memory_bank:
        memory_bank = json.loads(args.memory_bank.read_text())

    instances = []
    for row in eval_rows:
        matches = retrieve_memory(row, memory_bank, top_k=args.top_k, min_score=args.min_score) if memory_bank else []
        memory_context = render_memory_context(matches)
        instances.append(
            build_expert_instance(
                row,
                deployment_image=args.deployment_image,
                memory_context=memory_context,
            )
        )
    write_json(args.output_file, instances)


if __name__ == "__main__":
    main()

