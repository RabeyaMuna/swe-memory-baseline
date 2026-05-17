from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

REPO_PRESETS: dict[str, list[str]] = {
    "core_ci_repair": [
        "agno-agi/agno",
        "OpenAccess-AI-Collective/axolotl",
        "conan-io/conan",
        "flowersteam/flower",
    ],
}


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
    analyzed_doc = str(row.get("ci_problem_document") or "").strip()
    if analyzed_doc:
        return analyzed_doc
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
    failed_commands = _extract_failed_commands(logs)
    commands_block = "\n".join(f"- {cmd}" for cmd in failed_commands[:8]) if failed_commands else "- Unknown"

    return (
        "Repair the failing CI workflow for this repository.\n\n"
        f"Repository: {repo}\n"
        f"Failing commit: {row.get('sha_fail', '')}\n"
        f"Workflow name: {row.get('workflow_name', '')}\n"
        f"Workflow path: {row.get('workflow_path', '')}\n"
        f"Branch: {row.get('head_branch', '')}\n"
        f"Error type: {error_type_text or 'Unknown'}\n\n"
        "Failed commands inferred from the CI logs:\n"
        f"{commands_block}\n\n"
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


def _classify_failure_patterns(log_text: str, error_types: list[str], failed_commands: list[str]) -> list[str]:
    lowered = log_text.lower()
    patterns: list[str] = []
    if any("ruff" in cmd.lower() for cmd in failed_commands) or "ruff" in lowered:
        patterns.append("lint_or_format_failure")
    if any("pytest" in cmd.lower() for cmd in failed_commands) or "traceback" in lowered:
        patterns.append("test_or_runtime_failure")
    if any("mypy" in cmd.lower() for cmd in failed_commands) or "error:" in lowered:
        patterns.append("type_check_or_static_analysis")
    if any("import" in err.lower() for err in error_types) or "importerror" in lowered or "f401" in lowered:
        patterns.append("import_or_symbol_issue")
    if "whitespace" in lowered or "format" in lowered:
        patterns.append("formatting_issue")
    if not patterns:
        patterns.append("general_ci_failure")
    return patterns


def _infer_failed_tool(failed_commands: list[str], log_text: str) -> str:
    known_tools = ("ruff", "pytest", "mypy", "black", "isort", "flake8", "pylint", "make", "tox", "nox")
    lowered_log = log_text.lower()
    for command in failed_commands:
        lowered = command.lower()
        for tool in known_tools:
            if tool in lowered:
                return tool
    for tool in known_tools:
        if tool in lowered_log:
            return tool
    return "unknown"


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
    failed_commands = _extract_failed_commands(log_text)
    error_types = row.get("error_type") or []
    if not isinstance(error_types, list):
        error_types = [str(error_types)]
    ci_structured_context = row.get("ci_structured_context") if isinstance(row.get("ci_structured_context"), dict) else {}
    structured_effected_files = ci_structured_context.get("effected_files") if isinstance(ci_structured_context, dict) else []
    if isinstance(structured_effected_files, list):
        structured_target_files = [
            str(item.get("file") or "").strip()
            for item in structured_effected_files
            if isinstance(item, dict) and str(item.get("file") or "").strip()
        ]
    else:
        structured_target_files = []
    failed_tool = _infer_failed_tool(failed_commands, log_text)
    failure_patterns = _classify_failure_patterns(log_text, error_types, failed_commands)
    return {
        "memory_id": build_instance_id(row),
        "repo": _stable_repo_id(row),
        "workflow_name": str(row.get("workflow_name") or "").strip(),
        "workflow_path": str(row.get("workflow_path") or "").strip(),
        "sha_fail": str(row.get("sha_fail") or "").strip(),
        "error_types": [str(x).strip() for x in error_types if str(x).strip()],
        "changed_files": changed_files,
        "patch_files": patch_files,
        "failed_commands": failed_commands,
        "failed_tool": failed_tool,
        "log_excerpt": log_text[:1200],
        "patch_patterns": _classify_patch_patterns(diff_text),
        "failure_patterns": failure_patterns,
        "likely_target_files": structured_target_files[:5] or patch_files[:5] or changed_files[:5],
        "validation_commands": failed_commands[:4],
        "overall_failure_reasons": ci_structured_context.get("overall_failure_reasons") if isinstance(ci_structured_context, dict) else [],
        "mentioned_tokens": ci_structured_context.get("mentioned_tokens") if isinstance(ci_structured_context, dict) else [],
        "organized_log_summary": str(ci_structured_context.get("organized_log_summary") or "") if isinstance(ci_structured_context, dict) else "",
        "root_cause_hint": (
            "Failure resembles "
            + ", ".join(failure_patterns)
            + " with tool "
            + failed_tool
            + "."
        ),
        "fix_summary": (
            "Past fix touched files: "
            + ", ".join(patch_files[:5] or changed_files[:5])
            + ". Patch patterns: "
            + ", ".join(_classify_patch_patterns(diff_text))
            + ". Failed tool: "
            + failed_tool
        ).strip(),
        "search_text": " ".join(
            [
                _stable_repo_id(row),
                str(row.get("workflow_name") or ""),
                str(row.get("workflow_path") or ""),
                " ".join(str(x) for x in error_types),
                " ".join(str(x) for x in (ci_structured_context.get("overall_failure_reasons") or []))
                if isinstance(ci_structured_context, dict)
                else "",
                " ".join(str(x) for x in (ci_structured_context.get("mentioned_tokens") or []))
                if isinstance(ci_structured_context, dict)
                else "",
                str(ci_structured_context.get("organized_log_summary") or "")
                if isinstance(ci_structured_context, dict)
                else "",
                " ".join(changed_files[:10]),
                failed_tool,
                " ".join(failure_patterns),
                " ".join(failed_commands),
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
    current_repo = _stable_repo_id(row)
    score = 0.0
    if current_repo == str(memory_record.get("repo") or ""):
        score += 0.4

    row_error_types = row.get("error_type") or []
    if not isinstance(row_error_types, list):
        row_error_types = [str(row_error_types)]
    score += 0.2 * _overlap_ratio(row_error_types, memory_record.get("error_types") or [])

    if str(row.get("workflow_name") or "").strip().lower() == str(memory_record.get("workflow_name") or "").strip().lower():
        score += 0.1

    changed_files = [str(f) for f in (row.get("changed_files") or []) if str(f).strip()]
    score += 0.2 * _overlap_ratio(changed_files, memory_record.get("changed_files") or [])

    current_commands = _extract_failed_commands(normalize_logs(row, max_chars=1500))
    score += 0.1 * _overlap_ratio(current_commands, memory_record.get("failed_commands") or [])

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
    current_repo = _stable_repo_id(row)
    current_changed_files = [str(f) for f in (row.get("changed_files") or []) if str(f).strip()]
    current_error_types = row.get("error_type") or []
    if not isinstance(current_error_types, list):
        current_error_types = [str(current_error_types)]
    for record in memory_bank:
        score = score_memory_match(row, record)
        if score < min_score:
            continue
        if current_repo == str(record.get("repo") or "") and _overlap_ratio(
            current_changed_files, record.get("changed_files") or []
        ) > 0:
            memory_level = "L1"
        elif current_repo == str(record.get("repo") or "") and _overlap_ratio(
            current_error_types, record.get("error_types") or []
        ) > 0:
            memory_level = "L2"
        else:
            memory_level = "L3"
        scored.append({**record, "retrieval_score": score, "memory_level": memory_level})
    scored.sort(key=lambda item: float(item.get("retrieval_score") or 0.0), reverse=True)
    return scored[:top_k]


def render_memory_context(matches: list[dict[str, Any]]) -> str:
    if not matches:
        return ""
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for match in matches:
        grouped[str(match.get("memory_level") or "L3")].append(match)

    header = (
        "Retrieved prior CI repair memory. This is reusable prior experience only. "
        "Use it when it agrees with repository evidence, workflow definitions, and failing logs."
    )
    sections = [header]

    level_titles = {
        "L1": "L1 exact-ish prior cases",
        "L2": "L2 repo-level recurring patterns",
        "L3": "L3 cross-repo general repair patterns",
    }
    for level in ("L1", "L2", "L3"):
        level_matches = grouped.get(level) or []
        if not level_matches:
            continue
        sections.append(level_titles[level] + ":")
        for index, match in enumerate(level_matches, start=1):
            sections.append(
                "\n".join(
                    [
                        f"- Memory {index} score={float(match.get('retrieval_score') or 0.0):.2f} repo={match.get('repo', '')}",
                        f"  Workflow: {match.get('workflow_name', '')} [{match.get('workflow_path', '')}]",
                        "  Error types: " + ", ".join(match.get("error_types") or []),
                        "  Failed tool: " + str(match.get("failed_tool") or "unknown"),
                        "  Likely target files: " + ", ".join(match.get("likely_target_files") or []),
                        "  Validation commands: " + " | ".join((match.get("validation_commands") or [])[:3]),
                        "  Failure patterns: " + ", ".join(match.get("failure_patterns") or []),
                        "  Patch patterns: " + ", ".join(match.get("patch_patterns") or []),
                        "  Root-cause hint: " + str(match.get("root_cause_hint") or ""),
                        "  Reusable fix hint: " + str(match.get("fix_summary") or ""),
                    ]
                )
            )

    actionable_lines: list[str] = []
    best = matches[0]
    if best.get("likely_target_files"):
        actionable_lines.append(
            "Start inspection from: " + ", ".join((best.get("likely_target_files") or [])[:5])
        )
    if best.get("validation_commands"):
        actionable_lines.append(
            "Try validating with: " + " | ".join((best.get("validation_commands") or [])[:3])
        )
    if best.get("patch_patterns"):
        actionable_lines.append(
            "Prefer fix shapes like: " + ", ".join(best.get("patch_patterns") or [])
        )
    if actionable_lines:
        sections.append("Actionable guidance:")
        sections.extend(f"- {line}" for line in actionable_lines)
    return "\n\n".join(sections)


def build_expert_instance(
    row: dict[str, Any],
    *,
    deployment_type: str = "local",
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
            "deployment": (
                {
                    "type": "docker",
                    "image": deployment_image,
                }
                if deployment_type == "docker"
                else {
                    "type": "local",
                }
            ),
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
