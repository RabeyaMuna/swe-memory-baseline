from sweagent.benchmark.ci_repair_memory import (
    build_expert_instance,
    build_memory_record,
    build_problem_statement,
    render_memory_context,
    retrieve_memory,
    split_rows_by_repo,
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
    strong = build_memory_record(_row("huggingface", "diffusers", "1", "sha1", "src/target.py"))
    weak = build_memory_record(_row("canonical", "cloud-init", "2", "sha2", "cloudinit/cmd/main.py"))
    matches = retrieve_memory(target, [weak, strong], top_k=1, min_score=0.0)
    assert matches[0]["memory_id"].startswith("huggingface__diffusers")


def test_instance_contains_memory_context() -> None:
    row = _row("huggingface", "diffusers", "1", "sha1", "src/a.py")
    prompt = build_problem_statement(row)
    assert "Relevant failing logs" in prompt
    memory_context = render_memory_context([build_memory_record(row) | {"retrieval_score": 0.9}])
    instance = build_expert_instance(row, memory_context=memory_context)
    extra_fields = instance["problem_statement"]["extra_fields"]
    assert extra_fields["memory_context"]
    assert instance["problem_statement"]["id"] == "huggingface__diffusers-1"
