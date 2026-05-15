from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any


def _stable_repo_id(row: dict[str, Any]) -> str:
    return f"{row.get('repo_owner', '')}/{row.get('repo_name', '')}".strip("/")


def build_instance_id(row: dict[str, Any]) -> str:
    return f"{row.get('repo_owner', '')}__{row.get('repo_name', '')}-{row.get('id', '')}"


def _coerce_json_rows(path: str | Path) -> list[dict[str, Any]]:
    path = Path(path)
    if path.suffix == ".jsonl":
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    if path.suffix == ".json":
        data = json.loads(path.read_text())
        if not isinstance(data, list):
            msg = f"Expected JSON list in {path}"
            raise ValueError(msg)
        return data
    msg = f"Unsupported input file extension: {path.suffix}"
    raise ValueError(msg)


def write_json(path: str | Path, payload: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


def write_jsonl(path: str | Path, rows: list[dict[str, Any]]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def load_rows(path: str | Path) -> list[dict[str, Any]]:
    return _coerce_json_rows(path)


def normalize_logs(row: dict[str, Any], *, max_chars: int = 7000) -> str:
    logs = row.get("logs") or []
    chunks: list[str] = []
    for item in logs:
        if not isinstance(item, dict):
            continue
        step_name = str(item.get("step_name") or item.get("setp_name") or item.get("name") or "").strip()
        log_text = str(item.get("log") or "").strip()
        if not log_text:
            continue
        prefix = f"[{step_name}]\n" if step_name else ""
        chunks.append(prefix + log_text[:2000])
    text = "\n\n".join(chunks)
    return text[:max_chars]


def build_problem_statement(row: dict[str, Any]) -> str:
    repo = _stable_repo_id(row)
    changed_files = row.get("changed_files") or []
    files_block = "\n".join(f"- {f}" for f in changed_files[:20]) if changed_files else "- Unknown"
    workflow_yaml = str(row.get("workflow") or "").strip()
    logs = normalize_logs(row)
    error_types = row.get("error_type") or []
    if isinstance(error_types, list):
        error_type_text = ", ".join(str(x) for x in error_types if str(x).strip())
    else:
        error_type_text = str(error_types).strip()

    return (
        "Repair the failing CI workflow for this repository.\n\n"
        f"Repository: {repo}\n"
        f"Failing commit: {row.get('sha_fail', '')}\n"
        f"Workflow name: {row.get('workflow_name', '')}\n"
        f"Workflow path: {row.get('workflow_path', '')}\n"
        f"Branch: {row.get('head_branch', '')}\n"
        f"Error type: {error_type_text or 'Unknown'}\n\n"
        "Changed files in the failing change:\n"
        f"{files_block}\n\n"
        "Workflow definition:\n"
        f"{workflow_yaml}\n\n"
        "Relevant failing logs:\n"
        f"{logs}\n\n"
        "Task:\n"
        "Produce the minimal patch that fixes the CI failure at the failing commit. "
        "Do not use any hidden gold patch. Infer the repair from the repository, workflow, and logs."
    )


def _tokenize(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-zA-Z_][a-zA-Z0-9_./-]+", text.lower())
        if len(token) > 2 and token not in {"github", "actions", "python", "check", "error", "warning"}
    }


def _diff_files(diff_text: str) -> list[str]:
    files: list[str] = []
    for match in re.finditer(r"^\+\+\+ b/(.+)$", diff_text, flags=re.MULTILINE):
        files.append(match.group(1).strip())
    return files


def _classify_patch_patterns(diff_text: str) -> list[str]:
    lowered = diff_text.lower()
    patterns: list[str] = []
    if "import " in lowered or "from " in lowered:
        patterns.append("import_edit")
    if "ruff" in lowered or "format" in lowered:
        patterns.append("formatting")
    if re.search(r"^[+-]\s*$", diff_text, flags=re.MULTILINE):
        patterns.append("whitespace")
    if re.search(r"^[+-].*:$", diff_text, flags=re.MULTILINE):
        patterns.append("block_indentation")
    if not patterns:
        patterns.append("general_code_edit")
    return patterns


def _extract_failed_commands(log_text: str) -> list[str]:
    commands: list[str] = []
    for line in log_text.splitlines():
        clean = line.replace("\x1b", "")
        if "##[group]Run " in clean:
            commands.append(clean.split("##[group]Run ", 1)[1].strip())
        elif clean.startswith("$ "):
            commands.append(clean[2:].strip())
        if len(commands) >= 8:
            break
    deduped: list[str] = []
    for cmd in commands:
        if cmd and cmd not in deduped:
            deduped.append(cmd)
    return deduped


def build_memory_record(row: dict[str, Any]) -> dict[str, Any]:
    diff_text = str(row.get("diff") or "")
    log_text = normalize_logs(row, max_chars=4000)
    changed_files = [str(f) for f in (row.get("changed_files") or []) if str(f).strip()]
    patch_files = _diff_files(diff_text)
    error_types = row.get("error_type") or []
    if not isinstance(error_types, list):
        error_types = [str(error_types)]
    return {
        "memory_id": build_instance_id(row),
        "repo": _stable_repo_id(row),
        "workflow_name": str(row.get("workflow_name") or "").strip(),
        "workflow_path": str(row.get("workflow_path") or "").strip(),
        "sha_fail": str(row.get("sha_fail") or "").strip(),
        "error_types": [str(x).strip() for x in error_types if str(x).strip()],
        "changed_files": changed_files,
        "patch_files": patch_files,
        "failed_commands": _extract_failed_commands(log_text),
        "log_excerpt": log_text[:1200],
        "patch_patterns": _classify_patch_patterns(diff_text),
        "fix_summary": (
            "Past fix touched files: "
            + ", ".join(patch_files[:5] or changed_files[:5])
            + ". Patch patterns: "
            + ", ".join(_classify_patch_patterns(diff_text))
        ).strip(),
        "search_text": " ".join(
            [
                _stable_repo_id(row),
                str(row.get("workflow_name") or ""),
                str(row.get("workflow_path") or ""),
                " ".join(str(x) for x in error_types),
                " ".join(changed_files[:10]),
                " ".join(_extract_failed_commands(log_text)),
                log_text[:800],
            ]
        ),
    }


def _safe_int(value: Any) -> tuple[int, str]:
    text = str(value)
    try:
        return int(text), text
    except ValueError:
        return 10**9, text


def split_rows_by_repo(
    rows: list[dict[str, Any]],
    *,
    memory_ratio: float = 0.3,
    min_memory_per_repo: int = 5,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[_stable_repo_id(row)].append(row)

    seed_rows: list[dict[str, Any]] = []
    eval_rows: list[dict[str, Any]] = []
    for repo_id, repo_rows in grouped.items():
        sorted_rows = sorted(repo_rows, key=lambda row: _safe_int(row.get("id", "")))
        target_seed = max(min_memory_per_repo, int(round(len(sorted_rows) * memory_ratio)))
        if target_seed >= len(sorted_rows):
            target_seed = max(1, len(sorted_rows) - 1)
        seed_rows.extend(sorted_rows[:target_seed])
        eval_rows.extend(sorted_rows[target_seed:])
        if not eval_rows and sorted_rows:
            eval_rows.append(sorted_rows[-1])
            seed_rows = [row for row in seed_rows if row is not sorted_rows[-1]]
        if repo_id and not sorted_rows:
            continue
    return seed_rows, eval_rows


def _overlap_ratio(left: list[str], right: list[str]) -> float:
    left_set = {str(x).strip().lower() for x in left if str(x).strip()}
    right_set = {str(x).strip().lower() for x in right if str(x).strip()}
    if not left_set or not right_set:
        return 0.0
    return len(left_set & right_set) / len(left_set | right_set)


def score_memory_match(row: dict[str, Any], memory_record: dict[str, Any]) -> float:
    score = 0.0
    if _stable_repo_id(row) == str(memory_record.get("repo") or ""):
        score += 0.4

    row_error_types = row.get("error_type") or []
    if not isinstance(row_error_types, list):
        row_error_types = [str(row_error_types)]
    score += 0.2 * _overlap_ratio(row_error_types, memory_record.get("error_types") or [])

    if str(row.get("workflow_name") or "").strip().lower() == str(memory_record.get("workflow_name") or "").strip().lower():
        score += 0.1

    changed_files = [str(f) for f in (row.get("changed_files") or []) if str(f).strip()]
    score += 0.2 * _overlap_ratio(changed_files, memory_record.get("changed_files") or [])

    row_tokens = _tokenize(normalize_logs(row, max_chars=1500))
    mem_tokens = _tokenize(str(memory_record.get("search_text") or ""))
    if row_tokens and mem_tokens:
        score += 0.1 * (len(row_tokens & mem_tokens) / len(row_tokens | mem_tokens))

    if build_instance_id(row) == str(memory_record.get("memory_id") or ""):
        return 0.0
    return round(min(score, 1.0), 4)


def retrieve_memory(
    row: dict[str, Any],
    memory_bank: list[dict[str, Any]],
    *,
    top_k: int = 3,
    min_score: float = 0.15,
) -> list[dict[str, Any]]:
    scored: list[dict[str, Any]] = []
    for record in memory_bank:
        score = score_memory_match(row, record)
        if score < min_score:
            continue
        scored.append({**record, "retrieval_score": score})
    scored.sort(key=lambda item: float(item.get("retrieval_score") or 0.0), reverse=True)
    return scored[:top_k]


def render_memory_context(matches: list[dict[str, Any]]) -> str:
    if not matches:
        return ""
    blocks = [
        "Retrieved prior CI repair hints. These are non-binding hints, not ground truth. Ignore them if repository evidence disagrees."
    ]
    for index, match in enumerate(matches, start=1):
        blocks.append(
            "\n".join(
                [
                    f"Memory {index} (score={float(match.get('retrieval_score') or 0.0):.2f})",
                    f"Repo: {match.get('repo', '')}",
                    f"Workflow: {match.get('workflow_name', '')}",
                    "Error types: " + ", ".join(match.get("error_types") or []),
                    "Changed files: " + ", ".join((match.get("changed_files") or [])[:6]),
                    "Failed commands: " + " | ".join((match.get("failed_commands") or [])[:3]),
                    "Patch patterns: " + ", ".join(match.get("patch_patterns") or []),
                    "Reusable fix hint: " + str(match.get("fix_summary") or ""),
                ]
            )
        )
    return "\n\n".join(blocks)


def build_expert_instance(
    row: dict[str, Any],
    *,
    deployment_image: str = "python:3.11",
    memory_context: str = "",
) -> dict[str, Any]:
    instance_id = build_instance_id(row)
    github_url = f"https://github.com/{row.get('repo_owner', '')}/{row.get('repo_name', '')}"
    extra_fields = {
        "memory_context": memory_context,
        "repo_owner": str(row.get("repo_owner") or ""),
        "repo_name": str(row.get("repo_name") or ""),
        "workflow_name": str(row.get("workflow_name") or ""),
        "workflow_path": str(row.get("workflow_path") or ""),
        "sha_fail": str(row.get("sha_fail") or ""),
        "changed_files": row.get("changed_files") or [],
        "error_type": row.get("error_type") or [],
    }
    return {
        "env": {
            "deployment": {
                "type": "docker",
                "image": deployment_image,
            },
            "repo": {
                "type": "github",
                "github_url": github_url,
                "base_commit": str(row.get("sha_fail") or "HEAD"),
            },
        },
        "problem_statement": {
            "type": "text",
            "text": build_problem_statement(row),
            "id": instance_id,
            "extra_fields": extra_fields,
        },
    }

