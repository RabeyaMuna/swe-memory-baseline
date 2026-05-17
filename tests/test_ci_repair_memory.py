from sweagent.benchmark.ci_repair_memory import (
    REPO_PRESETS,
    build_expert_instance,
    build_memory_record,
    build_problem_statement,
    render_memory_context,
    retrieve_memory,
    split_rows_by_repo,
)
from sweagent.benchmark.ci_problem_document import (
    _chunk_log_entries,
    heuristic_ci_chunk_analysis,
    heuristic_ci_problem_document,
    heuristic_ci_problem_document_from_chunks,
)
from sweagent.benchmark.ci_memory_plugin import (
    build_hierarchical_memory_bank,
    render_hierarchical_memory_context,
    retrieve_hierarchical_memory,
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
    retrieval = retrieve_hierarchical_memory(target, bank, top_k_l1=1, min_l1=0.0, min_l2=0.0, min_l3=0.0)
    assert retrieval["l1_candidates"][0]["memory_id"].startswith("huggingface__diffusers")
    assert "L1" in retrieval["selected_memory_levels"]


def test_instance_contains_memory_context() -> None:
    row = _row("huggingface", "diffusers", "1", "sha1", "src/a.py")
    prompt = build_problem_statement(row)
    assert "Relevant failing logs" in prompt
    assert "Failed commands inferred from the CI logs" in prompt
    bank = build_hierarchical_memory_bank([row])
    retrieval = retrieve_hierarchical_memory(row, bank, top_k_l1=1, min_l1=0.0, min_l2=0.0, min_l3=0.0)
    memory_context = render_hierarchical_memory_context(retrieval)
    instance = build_expert_instance(row, memory_context=memory_context)
    extra_fields = instance["problem_statement"]["extra_fields"]
    assert extra_fields["memory_context"]
    assert instance["problem_statement"]["id"] == "huggingface__diffusers-1"
    assert "L1 Prior Case" in extra_fields["memory_context"]
    assert instance["env"]["deployment"]["type"] == "local"


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
