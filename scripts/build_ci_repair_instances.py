from __future__ import annotations

import argparse
import json
from pathlib import Path

from sweagent.benchmark.ci_memory_plugin import render_hierarchical_memory_context, retrieve_hierarchical_memory
from sweagent.benchmark.ci_repair_memory import (
    build_expert_instance,
    load_rows,
    write_json,
)
from sweagent.utils.config import load_environment_variables


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create SWE-agent expert instances, optionally with retrieved memory.")
    parser.add_argument("--eval-file", type=Path, required=True)
    parser.add_argument("--output-file", type=Path, required=True)
    parser.add_argument("--deployment-type", choices=["local", "docker"], default="local")
    parser.add_argument("--deployment-image", default="python:3.11")
    parser.add_argument("--memory-bank", type=Path)
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--min-score", type=float, default=0.15)
    parser.add_argument("--retrieval-report", type=Path)
    return parser.parse_args()


def main() -> None:
    load_environment_variables()
    args = _parse_args()
    eval_rows = load_rows(args.eval_file)
    memory_bank: dict = {}
    if args.memory_bank:
        memory_bank = json.loads(args.memory_bank.read_text())

    instances = []
    retrieval_report: list[dict] = []
    for row in eval_rows:
        retrieval = (
            retrieve_hierarchical_memory(
                row,
                memory_bank,
                top_k_l1=args.top_k,
                min_l1=args.min_score,
                min_l2=args.min_score,
                min_l3=args.min_score,
            )
            if memory_bank
            else {"use_memory": False}
        )
        memory_context = render_hierarchical_memory_context(retrieval)
        retrieval_report.append(
            {
                "instance_id": f"{row.get('repo_owner', '')}__{row.get('repo_name', '')}-{row.get('id', '')}",
                "repo": f"{row.get('repo_owner', '')}/{row.get('repo_name', '')}",
                "memory_used": bool(retrieval.get("use_memory")),
                "selected_memory_levels": retrieval.get("selected_memory_levels", []),
                "level_scores": retrieval.get("level_scores", {}),
                "weighted_similarity": retrieval.get("weighted_similarity", 0.0),
                "matches": {
                    "l1_candidates": retrieval.get("l1_candidates", []),
                    "l2_candidate": retrieval.get("l2_candidate"),
                    "l3_candidate": retrieval.get("l3_candidate"),
                },
                "memory_context": memory_context,
            }
        )
        instances.append(
            build_expert_instance(
                row,
                deployment_type=args.deployment_type,
                deployment_image=args.deployment_image,
                memory_context=memory_context,
            )
        )
    write_json(args.output_file, instances)
    if args.retrieval_report is not None:
        write_json(args.retrieval_report, retrieval_report)


if __name__ == "__main__":
    main()
