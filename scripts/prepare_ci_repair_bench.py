from __future__ import annotations

import argparse
from pathlib import Path

from datasets import load_dataset

from sweagent.benchmark.ci_repair_memory import REPO_PRESETS, split_rows_by_repo, write_json, write_jsonl
from sweagent.utils.config import load_environment_variables


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Split ci-repair-bench rows into memory-seed and evaluation partitions.")
    parser.add_argument("--dataset", default="ci-benchmark-user/ci-repair-bench")
    parser.add_argument("--split", default="train")
    parser.add_argument("--repos", nargs="*", default=[])
    parser.add_argument("--repo-preset", choices=sorted(REPO_PRESETS), default="core_ci_repair")
    parser.add_argument("--memory-ratio", type=float, default=0.3)
    parser.add_argument("--min-memory-per-repo", type=int, default=5)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    load_environment_variables()
    args = _parse_args()
    ds = load_dataset(args.dataset, split=args.split)  # type: ignore[arg-type]
    rows = [dict(row) for row in ds]
    selected_repos = args.repos or REPO_PRESETS.get(args.repo_preset, [])
    if selected_repos:
        allowed = set(selected_repos)
        rows = [row for row in rows if f"{row.get('repo_owner', '')}/{row.get('repo_name', '')}" in allowed]

    seed_rows, eval_rows = split_rows_by_repo(
        rows,
        memory_ratio=args.memory_ratio,
        min_memory_per_repo=args.min_memory_per_repo,
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(args.output_dir / "memory_seed_rows.jsonl", seed_rows)
    write_jsonl(args.output_dir / "eval_rows.jsonl", eval_rows)
    write_json(
        args.output_dir / "split_summary.json",
        {
            "dataset": args.dataset,
            "split": args.split,
            "repos": selected_repos,
            "repo_preset": args.repo_preset,
            "memory_ratio": args.memory_ratio,
            "min_memory_per_repo": args.min_memory_per_repo,
            "memory_seed_count": len(seed_rows),
            "eval_count": len(eval_rows),
        },
    )


if __name__ == "__main__":
    main()
