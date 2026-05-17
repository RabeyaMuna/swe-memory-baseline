from __future__ import annotations

import argparse
import os
from pathlib import Path

from sweagent.benchmark.ci_problem_document import analyze_ci_artifacts_with_llm, heuristic_ci_problem_document
from sweagent.benchmark.ci_repair_memory import load_rows, write_jsonl
from sweagent.utils.config import load_environment_variables


def _default_artifact_model() -> str:
    raw_model = (
        os.getenv("CI_ARTIFACT_ANALYSIS_MODEL")
        or os.getenv("MEMCI_LLM_MODEL")
        or "gpt-4o-mini"
    )
    model_aliases = {
        "MiniMax-M2.5": "openrouter/minimax/minimax-m2.5",
        "minimax-m2.5": "openrouter/minimax/minimax-m2.5",
        "minimax/minimax-m2.5": "openrouter/minimax/minimax-m2.5",
    }
    return model_aliases.get(raw_model, raw_model)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build structured CI problem documents from workflow YAML and CI logs.")
    parser.add_argument("--input-file", type=Path, required=True)
    parser.add_argument("--output-file", type=Path, required=True)
    parser.add_argument("--mode", choices=["heuristic", "llm"], default="heuristic")
    parser.add_argument("--model", default=_default_artifact_model())
    parser.add_argument(
        "--api-base",
        default=(
            os.getenv("MINIMAX_BASE_URL")
            or os.getenv("LITELLM_API_BASE")
            or os.getenv("OPENAI_API_BASE")
        ),
    )
    parser.add_argument(
        "--api-key",
        default=(
            os.getenv("MINIMAX_API_KEY")
            or os.getenv("LITELLM_API_KEY")
            or os.getenv("OPENAI_API_KEY")
        ),
    )
    parser.add_argument("--max-log-chars", type=int, default=12000)
    return parser.parse_args()


def main() -> None:
    load_environment_variables()
    args = _parse_args()
    rows = load_rows(args.input_file)
    output_rows: list[dict] = []
    for row in rows:
        if args.mode == "llm":
            analyzed = analyze_ci_artifacts_with_llm(
                row,
                model=args.model,
                api_base=args.api_base,
                api_key=args.api_key,
                max_log_chars=args.max_log_chars,
            )
        else:
            analyzed = heuristic_ci_problem_document(row)
        enriched = dict(row)
        enriched["ci_problem_document"] = analyzed["problem_document"]
        enriched["ci_structured_context"] = {
            "overall_failure_reasons": analyzed["overall_failure_reasons"],
            "overall_error_types": analyzed["overall_error_types"],
            "effected_files": analyzed["effected_files"],
            "failed_jobs": analyzed["failed_jobs"],
            "mentioned_tokens": analyzed["mentioned_tokens"],
            "organized_log_summary": analyzed["organized_log_summary"],
            "chunk_outputs": analyzed.get("chunk_outputs", []),
        }
        output_rows.append(enriched)
    write_jsonl(args.output_file, output_rows)


if __name__ == "__main__":
    main()
