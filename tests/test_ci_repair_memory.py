import json
import subprocess
import sys

from sweagent.benchmark.ci_repair_memory import (
    REPO_PRESETS,
    build_expert_instance,
    build_memory_record,
    build_problem_statement,
    render_memory_context,
    retrieve_memory,
    split_rows_by_repo,
    normalize_row,
)
from sweagent.benchmark.ci_problem_document import (
    _chunk_log_entries,
    heuristic_ci_chunk_analysis,
    heuristic_ci_problem_document,
    heuristic_ci_problem_document_from_chunks,
)
from sweagent.benchmark.ci_memory_plugin import (
    build_hierarchical_memory_bank,
    load_memory_bank,
    render_hierarchical_memory_context,
    retrieve_hierarchical_memory,
    summarize_hierarchical_memory_bank,
)


def _row(repo_owner: str, repo_name: str, issue_id: str, sha_fail: str, changed_file: str) -> dict:
    return {
        "repo_owner": repo_owner,
        "repo_name": repo_name,
        "id": issue_id,
        "sha_fail": sha_fail,
        "workflow_name": "Run code quality checks",
        "workflow_path": ".github/workflows/pr_quality.yml",
        "head_branch": "main",
        "workflow": "name: quality",
        "logs": [{"step_name": "lint", "log": "##[group]Run ruff check src\nruff check src\nF401 unused import"}],
        "diff": f"diff --git a/{changed_file} b/{changed_file}\n+++ b/{changed_file}\n@@\n-from x import y\n+from x import z\n",
        "changed_files": [changed_file],
        "error_type": ["Code Formatting"],
    }


def test_split_rows_by_repo_keeps_both_partitions() -> None:
    rows = [
        _row("huggingface", "diffusers", "1", "sha1", "src/a.py"),
        _row("huggingface", "diffusers", "2", "sha2", "src/b.py"),
        _row("huggingface", "diffusers", "3", "sha3", "src/c.py"),
        _row("canonical", "cloud-init", "1", "sha4", "cloudinit/a.py"),
        _row("canonical", "cloud-init", "2", "sha5", "cloudinit/b.py"),
    ]
    seed_rows, eval_rows = split_rows_by_repo(rows, memory_ratio=0.5, min_memory_per_repo=1)
    assert seed_rows
    assert eval_rows
    assert len(seed_rows) + len(eval_rows) == len(rows)


def test_memory_retrieval_prefers_same_repo_and_file() -> None:
    target = _row("huggingface", "diffusers", "9", "sha9", "src/target.py")
    bank = build_hierarchical_memory_bank(
        [
            _row("huggingface", "diffusers", "1", "sha1", "src/target.py"),
            _row("canonical", "cloud-init", "2", "sha2", "cloudinit/cmd/main.py"),
        ]
    )
    retrieval = retrieve_hierarchical_memory(target, bank, top_k_l1=1)
    assert retrieval["l1_candidates"][0]["memory_id"].startswith("huggingface__diffusers")
    assert "L1" in retrieval["selected_memory_levels"]
    assert retrieval["thresholds"]["L1"] == 0.30
    assert round(retrieval["weights"]["L1"], 3) == 0.600
    assert round(retrieval["weights"]["L2"], 3) == 0.300
    assert round(retrieval["weights"]["L3"], 3) == 0.100


def test_instance_contains_memory_context() -> None:
    row = _row("huggingface", "diffusers", "1", "sha1", "src/a.py")
    prompt = build_problem_statement(row)
    assert "Relevant failing logs" in prompt
    assert "Failed commands inferred from the CI logs" in prompt
    bank = build_hierarchical_memory_bank([row, _row("huggingface", "diffusers", "2", "sha2", "src/a.py")])
    retrieval = retrieve_hierarchical_memory(row, bank, top_k_l1=1)
    memory_context = render_hierarchical_memory_context(retrieval)
    instance = build_expert_instance(row, memory_context=memory_context)
    extra_fields = instance["problem_statement"]["extra_fields"]
    assert extra_fields["memory_context"]
    assert instance["problem_statement"]["id"] == "huggingface__diffusers-1"
    assert "L1 Prior Case" in extra_fields["memory_context"]
    assert instance["env"]["deployment"]["type"] == "local"


def test_ablation_weights_and_thresholds_match_external_contract() -> None:
    row = _row("huggingface", "diffusers", "1", "sha1", "src/a.py")
    bank = build_hierarchical_memory_bank([row, _row("huggingface", "diffusers", "2", "sha2", "src/a.py")])
    retrieval_l1 = retrieve_hierarchical_memory(row, bank, ablation_levels="L1")
    retrieval_l1_l2 = retrieve_hierarchical_memory(row, bank, ablation_levels="L1+L2")
    retrieval_all = retrieve_hierarchical_memory(row, bank, ablation_levels="L1+L2+L3")

    assert retrieval_l1["weights"] == {"L1": 1.0, "L2": 0.0, "L3": 0.0}
    assert retrieval_l1["thresholds"]["similarity_threshold"] == 0.55
    assert round(retrieval_l1_l2["weights"]["L1"], 3) == 0.667
    assert round(retrieval_l1_l2["weights"]["L2"], 3) == 0.333
    assert retrieval_l1_l2["weights"]["L3"] == 0.0
    assert retrieval_l1_l2["thresholds"]["similarity_threshold"] == 0.37
    assert round(retrieval_all["weights"]["L1"], 3) == 0.600
    assert round(retrieval_all["weights"]["L2"], 3) == 0.300
    assert round(retrieval_all["weights"]["L3"], 3) == 0.100
    assert retrieval_all["thresholds"]["similarity_threshold"] == 0.33


def test_instance_can_use_docker_deployment() -> None:
    row = _row("huggingface", "diffusers", "1", "sha1", "src/a.py")
    instance = build_expert_instance(row, deployment_type="docker", deployment_image="python:3.11")
    assert instance["env"]["deployment"]["type"] == "docker"
    assert instance["env"]["deployment"]["image"] == "python:3.11"


def test_core_repo_preset_contains_requested_repos() -> None:
    assert REPO_PRESETS["core_ci_repair"] == [
        "agno-agi/agno",
        "OpenAccess-AI-Collective/axolotl",
        "conan-io/conan",
        "flowersteam/flower",
    ]


def test_analyzed_ci_problem_document_is_preferred() -> None:
    row = _row("agno-agi", "agno", "1", "sha1", "src/app.py")
    analyzed = heuristic_ci_problem_document(row)
    assert "Workflow Context" in analyzed["problem_document"]
    assert "Organized CI Log Summary" in analyzed["problem_document"]
    row["ci_problem_document"] = analyzed["problem_document"]
    row["ci_structured_context"] = analyzed
    prompt = build_problem_statement(row)
    assert prompt == analyzed["problem_document"]
    record = build_memory_record(row)
    assert record["likely_target_files"]
    assert record["overall_failure_reasons"]
    assert record["mentioned_tokens"]
    assert record["organized_log_summary"]


def test_chunk_based_ci_document_builder() -> None:
    row = _row("agno-agi", "agno", "1", "sha1", "src/app.py")
    chunks = _chunk_log_entries(row, max_log_chars=4000, chunk_chars=40)
    assert chunks
    chunk_outputs = [heuristic_ci_chunk_analysis(row, chunk) for chunk in chunks]
    merged = heuristic_ci_problem_document_from_chunks(row, chunk_outputs)
    assert merged["chunk_outputs"]
    assert merged["problem_document"]
    assert merged["organized_log_summary"]
    assert "Workflow Context" in merged["problem_document"]
    assert "Chunked Failure Summary" in merged["problem_document"]


def test_hierarchical_memory_bank_builds_l1_l2_l3() -> None:
    rows = [
        _row("agno-agi", "agno", "1", "sha1", "src/app.py"),
        _row("agno-agi", "agno", "2", "sha2", "src/other.py"),
        _row("flowersteam", "flower", "3", "sha3", "src/flwr/app.py"),
    ]
    bank = build_hierarchical_memory_bank(rows)
    assert bank["l1"]
    assert bank["l2"]
    assert bank["l3"]


def test_normalize_external_issue_row_supports_direct_eval_file() -> None:
    row = normalize_row(
        {
            "id": "192",
            "repo_owner": "agno-agi",
            "repo_name": "agno",
            "sha_fail": "sha1",
            "workflow_name": "Validation",
            "workflow_path": ".github/workflows/test.yml",
            "workflow": "name: Validation",
            "error_types": ["Dependency Error"],
            "failed_cmd": ["ruff check", "pytest"],
            "failed_tool": ["ruff", "pytest"],
            "ground_truth_files": ["src/a.py"],
            "error_context_summary": "Import error broke CI.",
            "logs_summary": "pytest failed during collection.",
        }
    )
    assert row["error_type"] == ["Dependency Error"]
    assert row["changed_files"] == ["src/a.py"]
    assert row["ci_structured_context"]["overall_failure_reasons"]
    prompt = build_problem_statement(row)
    assert "ruff check" in prompt
    assert "pytest failed during collection." in prompt


def test_load_memory_bank_supports_external_split_files(tmp_path) -> None:
    (tmp_path / "failure_memory.json").write_text(
        json.dumps(
            [
                {
                    "issue_id": "192",
                    "sha_fail": "sha1",
                    "repo": "agno",
                    "repo_name": "agno",
                    "workflow_path": ".github/workflows/test.yml",
                    "workflow_name": "Validation",
                    "file": "src/a.py",
                    "error_type": "Dependency Error",
                    "failure_pattern": "import-error",
                    "failure_reason": "Import error broke CI.",
                    "fix_strategy": "Update the import.",
                    "fix_pattern": ["import-fix"],
                    "failed_tool": ["pytest"],
                    "failed_cmd": ["pytest"],
                    "dependent_files": [],
                }
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "repo_memory.json").write_text(
        json.dumps(
            [
                {
                    "sha_fail": "sha1",
                    "issue_ids": ["192"],
                    "repo": "agno",
                    "repo_name": "agno",
                    "error_type": "Dependency Error",
                    "workflow_path": ".github/workflows/test.yml",
                    "failed_tool": ["pytest"],
                    "failed_cmd": ["pytest"],
                    "failure_pattern": "import-error",
                    "failure_reason": "Import error broke CI.",
                    "fix_strategy": "Update the import.",
                    "fix_pattern": ["import-fix"],
                    "files": [{"file": "src/a.py", "failure_reason": "Import error broke CI."}],
                }
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "cross_memory.json").write_text(
        json.dumps(
            [
                {
                    "sha_fail": "sha1",
                    "repo": "agno",
                    "repos": ["agno"],
                    "error_type": "Dependency Error",
                    "failure_pattern": "import-error",
                    "failure_reasons": ["Import error broke CI."],
                    "fix_strategy": "Update the import.",
                    "failed_tool": ["pytest"],
                    "principle": "Imports can drift after upstream API changes.",
                }
            ]
        ),
        encoding="utf-8",
    )
    bank = load_memory_bank(tmp_path)
    assert bank["l1"][0]["repo"] == "agno"
    assert bank["l2"][0]["error_type"] == "Dependency Error"
    assert bank["l3"][0]["failed_tool"] == "pytest"


def test_build_instances_script_supports_external_eval_and_memory_files(tmp_path) -> None:
    eval_file = tmp_path / "memory_eval_issues.json"
    output_file = tmp_path / "eval_instances_memory.json"
    retrieval_report = tmp_path / "retrieval_report.json"
    eval_file.write_text(
        json.dumps(
            [
                {
                    "id": "200",
                    "repo_owner": "agno-agi",
                    "repo_name": "agno",
                    "sha_fail": "sha2",
                    "workflow_name": "Validation",
                    "workflow_path": ".github/workflows/test.yml",
                    "workflow": "name: Validation",
                    "error_types": ["Dependency Error"],
                    "failed_cmd": ["pytest"],
                    "failed_tool": ["pytest"],
                    "changed_files": ["src/a.py"],
                    "error_context_summary": "Import error broke CI.",
                    "logs_summary": "pytest failed during collection.",
                }
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "failure_memory.json").write_text(
        json.dumps(
            [
                {
                    "issue_id": "192",
                    "sha_fail": "sha1",
                    "repo": "agno",
                    "repo_name": "agno",
                    "workflow_path": ".github/workflows/test.yml",
                    "workflow_name": "Validation",
                    "file": "src/a.py",
                    "error_type": "Dependency Error",
                    "failure_pattern": "import-error",
                    "failure_reason": "Import error broke CI.",
                    "fix_strategy": "Update the import.",
                    "fix_pattern": ["import-fix"],
                    "failed_tool": ["pytest"],
                    "failed_cmd": ["pytest"],
                    "dependent_files": [],
                }
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "repo_memory.json").write_text(json.dumps([]), encoding="utf-8")
    (tmp_path / "cross_memory.json").write_text(json.dumps([]), encoding="utf-8")

    subprocess.run(
        [
            sys.executable,
            "scripts/build_ci_repair_instances.py",
            "--eval-file",
            str(eval_file),
            "--memory-mode",
            "memory",
            "--memory-bank",
            str(tmp_path),
            "--output-file",
            str(output_file),
            "--retrieval-report",
            str(retrieval_report),
            "--ablation-levels",
            "L1+L2+L3",
        ],
        check=True,
    )

    instances = json.loads(output_file.read_text(encoding="utf-8"))
    report = json.loads(retrieval_report.read_text(encoding="utf-8"))
    assert instances[0]["problem_statement"]["extra_fields"]["memory_context"]
    assert report[0]["memory_used"] is True
    assert round(report[0]["weights"]["L1"], 3) == 0.6
    assert report[0]["thresholds"]["similarity_threshold"] == 0.33


def test_hierarchical_memory_bank_summary_matches_compatibility_shape() -> None:
    rows = [
        _row("agno-agi", "agno", "1", "sha1", "src/app.py"),
        _row("agno-agi", "agno", "2", "sha2", "src/other.py"),
        _row("flowersteam", "flower", "3", "sha3", "src/flwr/app.py"),
    ]
    bank = build_hierarchical_memory_bank(rows)
    summary = summarize_hierarchical_memory_bank(
        bank,
        seed_file="/tmp/memory_seed_issues.json",
        analysis_file="/tmp/seed_log_details.json",
        model_key="heuristic",
    )
    assert summary["model_key"] == "heuristic"
    assert summary["seed_file"].endswith("memory_seed_issues.json")
    assert summary["analysis_file"].endswith("seed_log_details.json")
    assert summary["total_issues"] == 3
    assert summary["processed"] == 3
    assert summary["skipped"] == 0
    assert summary["memory_counts"]["L1_failure_memory"] == len(bank["l1"])
    assert summary["memory_counts"]["L2_repo_memory"] == len(bank["l2"])
    assert summary["memory_counts"]["L3_cross_memory"] == len(bank["l3"])


def test_build_memory_bank_script_writes_compatibility_artifacts(tmp_path) -> None:
    rows = [
        _row("agno-agi", "agno", "1", "sha1", "src/app.py"),
        _row("agno-agi", "agno", "2", "sha2", "src/other.py"),
        _row("flowersteam", "flower", "3", "sha3", "src/flwr/app.py"),
    ]
    seed_file = tmp_path / "memory_seed_issues.json"
    output_file = tmp_path / "memory_bank.json"
    seed_file.write_text(json.dumps(rows), encoding="utf-8")

    subprocess.run(
        [
            sys.executable,
            "scripts/build_ci_repair_memory_bank.py",
            "--seed-file",
            str(seed_file),
            "--output-file",
            str(output_file),
        ],
        check=True,
    )

    summary = json.loads((tmp_path / "memory_bank_summary.json").read_text(encoding="utf-8"))
    assert output_file.exists()
    assert (tmp_path / "failure_memory.json").exists()
    assert (tmp_path / "repo_memory.json").exists()
    assert (tmp_path / "cross_memory.json").exists()
    assert summary["seed_file"] == str(seed_file)
    assert summary["memory_counts"]["L1_failure_memory"] > 0
