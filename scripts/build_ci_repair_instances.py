from __future__ import annotations

import argparse
from pathlib import Path

from sweagent.benchmark.ci_memory_plugin import (
    load_memory_bank,
    render_hierarchical_memory_context,
    retrieve_hierarchical_memory,
)
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
    parser.add_argument("--memory-mode", choices=["baseline", "memory"], default="baseline")
    parser.add_argument("--memory-bank", type=Path)
    parser.add_argument("--ablation-levels", choices=["L1", "L1+L2", "L1+L2+L3"], default="L1+L2+L3")
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--retrieval-report", type=Path)
    return parser.parse_args()


def main() -> None:
    load_environment_variables()
    args = _parse_args()
    eval_rows = load_rows(args.eval_file)
    memory_bank: dict = {}
    if args.memory_mode == "memory" and args.memory_bank:
        memory_bank = load_memory_bank(args.memory_bank)

    instances = []
    retrieval_report: list[dict] = []
    for row in eval_rows:
        retrieval = (
            retrieve_hierarchical_memory(
                row,
                memory_bank,
                top_k_l1=args.top_k,
                ablation_levels=args.ablation_levels,
            )
            if args.memory_mode == "memory" and memory_bank
            else {
                "enabled": args.memory_mode == "memory",
                "use_memory": False,
                "reason": "memory_disabled" if args.memory_mode == "baseline" else "memory_bank_missing",
                "weights": {},
                "thresholds": {},
                "level_scores": {"L1": 0.0, "L2": 0.0, "L3": 0.0},
                "weighted_similarity": 0.0,
                "selected_memory_levels": [],
                "l1_matches": [],
                "l2_matches": [],
                "l3_matches": [],
                "l1_candidates": [],
                "l2_candidate": None,
                "l3_candidate": None,
                "matches": [],
                "memory_context": "",
                "ablation_levels": args.ablation_levels,
            }
        )
        memory_context = render_hierarchical_memory_context(retrieval)
        retrieval_report.append(
            {
                "instance_id": f"{row.get('repo_owner', '')}__{row.get('repo_name', '')}-{row.get('id', '')}",
                "repo": f"{row.get('repo_owner', '')}/{row.get('repo_name', '')}",
                "memory_mode": args.memory_mode,
                "ablation_levels": args.ablation_levels if args.memory_mode == "memory" else None,
                "memory_used": bool(retrieval.get("use_memory")),
                "reason": retrieval.get("reason", ""),
                "thresholds": retrieval.get("thresholds", {}),
                "weights": retrieval.get("weights", {}),
                "selected_memory_levels": retrieval.get("selected_memory_levels", []),
                "level_scores": retrieval.get("level_scores", {}),
                "weighted_similarity": retrieval.get("weighted_similarity", 0.0),
                "candidate_files": retrieval.get("candidate_files", []),
                "high_level_hints": retrieval.get("high_level_hints", []),
                "matches": {
                    "l1_matches": retrieval.get("l1_matches", []),
                    "l2_matches": retrieval.get("l2_matches", []),
                    "l3_matches": retrieval.get("l3_matches", []),
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
