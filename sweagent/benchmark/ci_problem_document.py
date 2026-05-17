from __future__ import annotations

import json
import re
from typing import Any

import litellm

from sweagent.benchmark.ci_repair_memory import _extract_failed_commands, _stable_repo_id, normalize_logs


def _normalize_text_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value or "").strip()
    return [text] if text else []


def _normalize_effected_files(value: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not isinstance(value, list):
        return out
    for item in value:
        if not isinstance(item, dict):
            continue
        file_path = str(item.get("file") or "").strip()
        if not file_path:
            continue
        out.append(
            {
                "file": file_path,
                "reason": str(item.get("reason") or "").strip(),
                "issue_type": str(item.get("issue_type") or item.get("error_subtype") or item.get("error_type") or "").strip(),
                "error_type": str(item.get("error_type") or "").strip(),
                "error_subtype": str(item.get("error_subtype") or "").strip(),
                "failed_tools": _normalize_text_list(item.get("failed_tools")),
                "failed_command": str(item.get("failed_command") or "").strip(),
            }
        )
    return out


def _normalize_failed_jobs(value: Any) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    if not isinstance(value, list):
        return out
    for item in value:
        if not isinstance(item, dict):
            continue
        step = str(item.get("step") or item.get("step_name") or "").strip()
        failed_command = str(item.get("failed_command") or "").strip()
        if not step and not failed_command:
            continue
        out.append({"step": step, "failed_command": failed_command})
    return out


def _split_log_text(log_text: str, *, chunk_chars: int = 3000, overlap_chars: int = 300) -> list[str]:
    text = str(log_text or "").strip()
    if not text:
        return []
    if len(text) <= chunk_chars:
        return [text]
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + chunk_chars)
        chunks.append(text[start:end])
        if end >= len(text):
            break
        start = max(end - overlap_chars, start + 1)
    return chunks


def _chunk_log_entries(row: dict[str, Any], *, max_log_chars: int = 12000, chunk_chars: int = 3000) -> list[dict[str, str]]:
    entries = row.get("logs") or []
    output: list[dict[str, str]] = []
    if isinstance(entries, list):
        for item in entries:
            if not isinstance(item, dict):
                continue
            step_name = str(item.get("step_name") or item.get("setp_name") or item.get("name") or "ci").strip()
            log_text = str(item.get("log") or "").strip()[:max_log_chars]
            if not log_text:
                continue
            for index, chunk in enumerate(_split_log_text(log_text, chunk_chars=chunk_chars), start=1):
                output.append({"step_name": step_name, "chunk_id": f"{step_name}:{index}", "log_chunk": chunk})
    if output:
        return output
    merged = normalize_logs(row, max_chars=max_log_chars)
    return [{"step_name": "ci", "chunk_id": "ci:1", "log_chunk": chunk} for chunk in _split_log_text(merged, chunk_chars=chunk_chars)]


def build_ci_log_chunk_prompt(row: dict[str, Any], chunk: dict[str, str]) -> str:
    error_types = row.get("error_type") or []
    if isinstance(error_types, list):
        error_type_text = ", ".join(str(item) for item in error_types if str(item).strip())
    else:
        error_type_text = str(error_types).strip()
    return f"""
You are a CI log chunk analyzer.

Analyze one CI log chunk and return only the important failure signals in strict JSON.

Return STRICT JSON only:

{{
  "step_name": "{chunk.get('step_name', 'ci')}",
  "chunk_summary": "2 to 4 sentence summary of what this chunk shows.",
  "mentioned_tokens": [
    "tools, error codes, modules, symbols, short failure phrases"
  ],
  "effected_files": [
    {{
      "file": "repo/relative/path.py",
      "reason": "why this file is mentioned in this chunk",
      "issue_type": "short issue type",
      "error_type": "broad category",
      "error_subtype": "specific subtype",
      "failed_tools": ["ruff"],
      "failed_command": "ruff check src/"
    }}
  ],
  "failed_jobs": [
    {{
      "step": "job or step name",
      "failed_command": "exact or best inferred command"
    }}
  ]
}}

Rules:
- Use only evidence from this chunk.
- Be concise.
- Do not suggest fixes.
- Normalize file paths where possible.
- `mentioned_tokens` should contain only short high-signal clues.

Repository: {_stable_repo_id(row)}
Workflow name: {row.get("workflow_name", "")}
Workflow path: {row.get("workflow_path", "")}
Provided error types: {error_type_text}
Step name: {chunk.get("step_name", "ci")}

Log chunk:
{chunk.get("log_chunk", "")}
""".strip()


def build_ci_problem_document_prompt(row: dict[str, Any], *, max_log_chars: int = 12000) -> str:
    logs = normalize_logs(row, max_chars=max_log_chars)
    changed_files = row.get("changed_files") or []
    if isinstance(changed_files, list):
        changed_files_block = "\n".join(f"- {file_path}" for file_path in changed_files[:30]) or "- Unknown"
    else:
        changed_files_block = f"- {changed_files}"
    error_types = row.get("error_type") or []
    if isinstance(error_types, list):
        error_type_text = ", ".join(str(item) for item in error_types if str(item).strip())
    else:
        error_type_text = str(error_types).strip()

    return f"""
You are a CI artifact analysis engine.

Your job is to convert noisy CI artifacts into a compact, structured repair document for a coding agent.

Return STRICT JSON only:

{{
  "overall_failure_reasons": [
    "2 to 5 concise reasons explaining why the CI failed"
  ],
  "overall_error_types": [
    "broad error class such as Code Formatting, Code Styling, Test Failure, Runtime Error, Type Checking, Dependency Error, Workflow Error"
  ],
  "effected_files": [
    {{
      "file": "repo/relative/path.py",
      "reason": "why this file appears relevant from workflow/log evidence",
      "issue_type": "short issue type",
      "error_type": "broad category",
      "error_subtype": "specific subtype",
      "failed_tools": ["ruff"],
      "failed_command": "ruff check src/"
    }}
  ],
  "failed_jobs": [
    {{
      "step": "job or step name",
      "failed_command": "exact or best inferred command"
    }}
  ],
  "mentioned_tokens": [
    "important tools, error codes, symbols, modules, or short failure phrases"
  ],
  "organized_log_summary": "A compact organized summary of the CI logs, grouped by the main failing signals.",
  "problem_document": "A compact developer-facing CI problem statement combining workflow facts and failure facts."
}}

Rules:
- Use only evidence from the provided workflow YAML, changed files, and CI logs.
- Keep output concise and structured.
- Normalize file paths to repo-relative form where possible.
- Prefer exact failing commands when visible in logs or workflow YAML.
- Do not suggest fixes.
- `mentioned_tokens` should contain short high-signal retrieval clues only.
- `organized_log_summary` should summarize the failure in a clean readable form, not raw log spam.
- `problem_document` should be directly usable as a problem statement for an automated repair agent.

Repository: {_stable_repo_id(row)}
Failing commit: {row.get("sha_fail", "")}
Workflow name: {row.get("workflow_name", "")}
Workflow path: {row.get("workflow_path", "")}
Branch: {row.get("head_branch", "")}
Provided error types: {error_type_text}

Changed files:
{changed_files_block}

Workflow YAML:
{row.get("workflow", "")}

CI logs:
{logs}
""".strip()


def build_ci_merge_prompt(row: dict[str, Any], chunk_outputs: list[dict[str, Any]]) -> str:
    changed_files = row.get("changed_files") or []
    if isinstance(changed_files, list):
        changed_files_block = "\n".join(f"- {file_path}" for file_path in changed_files[:30]) or "- Unknown"
    else:
        changed_files_block = f"- {changed_files}"
    error_types = row.get("error_type") or []
    if isinstance(error_types, list):
        error_type_text = ", ".join(str(item) for item in error_types if str(item).strip())
    else:
        error_type_text = str(error_types).strip()
    return f"""
You are a CI artifact merger.

You are given chunk-level CI log analyses. Merge them into one organized CI problem document.

Return STRICT JSON only:

{{
  "overall_failure_reasons": [
    "2 to 5 concise reasons explaining why the CI failed"
  ],
  "overall_error_types": [
    "broad error class"
  ],
  "effected_files": [
    {{
      "file": "repo/relative/path.py",
      "reason": "why this file is relevant overall",
      "issue_type": "short issue type",
      "error_type": "broad category",
      "error_subtype": "specific subtype",
      "failed_tools": ["ruff"],
      "failed_command": "ruff check src/"
    }}
  ],
  "failed_jobs": [
    {{
      "step": "job or step name",
      "failed_command": "exact or best inferred command"
    }}
  ],
  "mentioned_tokens": [
    "important tools, error codes, symbols, modules, or short failure phrases"
  ],
  "organized_log_summary": "A clean merged summary of the chunk outputs.",
  "problem_document": "A compact developer-facing CI problem statement combining workflow facts and merged failure facts."
}}

Rules:
- Merge duplicate files, jobs, and tokens.
- Prefer consistent broad error types.
- Do not suggest fixes.
- Keep the final document compact and useful for a coding agent.

Repository: {_stable_repo_id(row)}
Failing commit: {row.get("sha_fail", "")}
Workflow name: {row.get("workflow_name", "")}
Workflow path: {row.get("workflow_path", "")}
Provided error types: {error_type_text}

Changed files:
{changed_files_block}

Workflow YAML:
{row.get("workflow", "")}

Chunk analyses:
{json.dumps(chunk_outputs, indent=2)}
""".strip()


def heuristic_ci_problem_document(row: dict[str, Any]) -> dict[str, Any]:
    repo = _stable_repo_id(row)
    logs = normalize_logs(row, max_chars=7000)
    failed_commands = _extract_failed_commands(logs)
    error_types = row.get("error_type") or []
    if not isinstance(error_types, list):
        error_types = [str(error_types)]
    changed_files = [str(file_path).strip() for file_path in (row.get("changed_files") or []) if str(file_path).strip()]
    workflow_name = str(row.get("workflow_name") or "").strip()
    workflow_path = str(row.get("workflow_path") or "").strip()

    failed_jobs: list[dict[str, str]] = []
    for item in row.get("logs") or []:
        if not isinstance(item, dict):
            continue
        step = str(item.get("step_name") or item.get("setp_name") or item.get("name") or "").strip()
        command = ""
        item_log = str(item.get("log") or "")
        extracted = _extract_failed_commands(item_log)
        if extracted:
            command = extracted[0]
        if step or command:
            failed_jobs.append({"step": step or "ci", "failed_command": command})

    if not failed_jobs and failed_commands:
        failed_jobs = [{"step": "ci", "failed_command": failed_commands[0]}]

    effected_files = [
        {
            "file": file_path,
            "reason": "File appears in the benchmark changed_files list and may be relevant to the failure.",
            "issue_type": error_types[0] if error_types else "CI failure",
            "error_type": error_types[0] if error_types else "Unknown",
            "error_subtype": "",
            "failed_tools": [failed_commands[0].split()[0]] if failed_commands else [],
            "failed_command": failed_commands[0] if failed_commands else "",
        }
        for file_path in changed_files[:10]
    ]

    overall_failure_reasons = []
    if failed_commands:
        overall_failure_reasons.append(f"Validation failed while running: {failed_commands[0]}")
    if error_types:
        overall_failure_reasons.append(f"Benchmark-provided error type: {', '.join(error_types)}")
    if not overall_failure_reasons:
        overall_failure_reasons.append("CI validation failed according to the provided workflow logs.")

    log_excerpt_lines = []
    for line in logs.splitlines():
        if any(token in line.lower() for token in ("error", "failed", "traceback", "f401", "ruff", "pytest", "mypy")):
            log_excerpt_lines.append(line)
        if len(log_excerpt_lines) >= 12:
            break
    log_excerpt = "\n".join(log_excerpt_lines)[:1500]

    mentioned_tokens: list[str] = []
    token_candidates = re.findall(r"[A-Za-z_][A-Za-z0-9_./:-]+", logs)
    for token in token_candidates:
        lowered = token.lower()
        if len(token) < 4:
            continue
        if lowered in {"process", "completed", "error", "failed", "warning", "group", "step_name"}:
            continue
        if token not in mentioned_tokens:
            mentioned_tokens.append(token)
        if len(mentioned_tokens) >= 20:
            break

    organized_log_summary_parts: list[str] = []
    if failed_jobs:
        organized_log_summary_parts.append(
            "Failed steps: " + "; ".join(
                f"{job.get('step')}: {job.get('failed_command') or 'command not recovered'}"
                for job in failed_jobs[:5]
            )
        )
    if error_types:
        organized_log_summary_parts.append("Error classes: " + ", ".join(error_types[:5]))
    if changed_files:
        organized_log_summary_parts.append("Files to inspect first: " + ", ".join(changed_files[:8]))
    if log_excerpt:
        organized_log_summary_parts.append("Key log lines:\n" + log_excerpt)
    organized_log_summary = "\n\n".join(organized_log_summary_parts).strip()

    problem_document = (
        "Repair the failing CI workflow for this repository.\n\n"
        "Workflow Context\n"
        f"- Repository: {repo}\n"
        f"- Failing commit: {row.get('sha_fail', '')}\n"
        f"- Workflow: {workflow_name or 'Unknown'}\n"
        f"- Workflow path: {workflow_path or 'Unknown'}\n"
        f"- Error types: {', '.join(error_types) if error_types else 'Unknown'}\n\n"
        "Likely Failing Commands\n"
        + ("\n".join(f"- {command}" for command in failed_commands[:6]) if failed_commands else "- Unknown")
        + "\n\n"
        + "Affected Files\n"
        + ("\n".join(f"- {file_path}" for file_path in changed_files[:12]) if changed_files else "- Unknown")
        + "\n\n"
        + "Failure Summary\n"
        + ("\n".join(f"- {reason}" for reason in overall_failure_reasons) if overall_failure_reasons else "- Unknown")
        + "\n\n"
        + "Mentioned Tokens\n"
        + ("\n".join(f"- {token}" for token in mentioned_tokens[:15]) if mentioned_tokens else "- None")
        + "\n\n"
        + "Workflow YAML\n"
        + str(row.get("workflow") or "")
        + "\n\n"
        + "Organized CI Log Summary\n"
        + (organized_log_summary or "No organized summary available.")
    )

    return {
        "overall_failure_reasons": overall_failure_reasons,
        "overall_error_types": error_types,
        "effected_files": effected_files,
        "failed_jobs": failed_jobs,
        "mentioned_tokens": mentioned_tokens,
        "organized_log_summary": organized_log_summary,
        "problem_document": problem_document,
    }


def heuristic_ci_chunk_analysis(row: dict[str, Any], chunk: dict[str, str]) -> dict[str, Any]:
    log_text = str(chunk.get("log_chunk") or "")
    step_name = str(chunk.get("step_name") or "ci")
    failed_commands = _extract_failed_commands(log_text)
    error_types = row.get("error_type") or []
    if not isinstance(error_types, list):
        error_types = [str(error_types)]
    mentioned_tokens: list[str] = []
    for token in re.findall(r"[A-Za-z_][A-Za-z0-9_./:-]+", log_text):
        lowered = token.lower()
        if len(token) < 4:
            continue
        if lowered in {"process", "completed", "error", "failed", "warning", "group"}:
            continue
        if token not in mentioned_tokens:
            mentioned_tokens.append(token)
        if len(mentioned_tokens) >= 12:
            break
    file_matches = []
    for token in mentioned_tokens:
        if "/" in token and "." in token:
            file_matches.append(token)
    chunk_summary_lines = []
    if failed_commands:
        chunk_summary_lines.append(f"Likely failing command: {failed_commands[0]}")
    if error_types:
        chunk_summary_lines.append(f"Error class hints: {', '.join(error_types[:3])}")
    key_lines = []
    for line in log_text.splitlines():
        if any(token in line.lower() for token in ("error", "failed", "traceback", "exception", "f401", "ruff", "pytest", "mypy")):
            key_lines.append(line.strip())
        if len(key_lines) >= 5:
            break
    if key_lines:
        chunk_summary_lines.append("Key lines: " + " | ".join(key_lines))
    return {
        "step_name": step_name,
        "chunk_summary": "\n".join(chunk_summary_lines).strip(),
        "mentioned_tokens": mentioned_tokens,
        "effected_files": [
            {
                "file": file_path,
                "reason": "File path is mentioned directly in this CI log chunk.",
                "issue_type": error_types[0] if error_types else "CI failure",
                "error_type": error_types[0] if error_types else "Unknown",
                "error_subtype": "",
                "failed_tools": [failed_commands[0].split()[0]] if failed_commands else [],
                "failed_command": failed_commands[0] if failed_commands else "",
            }
            for file_path in file_matches[:5]
        ],
        "failed_jobs": [{"step": step_name, "failed_command": failed_commands[0]}] if failed_commands else [],
    }


def heuristic_ci_problem_document_from_chunks(row: dict[str, Any], chunk_outputs: list[dict[str, Any]]) -> dict[str, Any]:
    base = heuristic_ci_problem_document(row)
    mentioned_tokens: list[str] = []
    effected_files_map: dict[str, dict[str, Any]] = {}
    failed_jobs_map: dict[tuple[str, str], dict[str, str]] = {}
    chunk_summaries: list[str] = []
    for chunk in chunk_outputs:
        if chunk.get("chunk_summary"):
            chunk_summaries.append(str(chunk["chunk_summary"]).strip())
        for token in _normalize_text_list(chunk.get("mentioned_tokens")):
            if token not in mentioned_tokens:
                mentioned_tokens.append(token)
        for item in _normalize_effected_files(chunk.get("effected_files")):
            file_path = item["file"]
            if file_path not in effected_files_map:
                effected_files_map[file_path] = item
        for job in _normalize_failed_jobs(chunk.get("failed_jobs")):
            key = (job.get("step", ""), job.get("failed_command", ""))
            if key not in failed_jobs_map:
                failed_jobs_map[key] = job
    organized_log_summary = "\n\n".join(chunk_summaries).strip() or base["organized_log_summary"]
    overall_failure_reasons = base["overall_failure_reasons"]
    if chunk_summaries:
        overall_failure_reasons = overall_failure_reasons + [f"Chunk evidence count: {len(chunk_summaries)}"]
    likely_commands = []
    for job in list(failed_jobs_map.values())[:6]:
        command = str(job.get("failed_command") or "").strip()
        if command:
            likely_commands.append(command)
    problem_document = (
        "Repair the failing CI workflow for this repository.\n\n"
        "Workflow Context\n"
        f"- Repository: {_stable_repo_id(row)}\n"
        f"- Failing commit: {row.get('sha_fail', '')}\n"
        f"- Workflow: {row.get('workflow_name', '') or 'Unknown'}\n"
        f"- Workflow path: {row.get('workflow_path', '') or 'Unknown'}\n"
        f"- Error types: {', '.join(base['overall_error_types']) if base['overall_error_types'] else 'Unknown'}\n\n"
        "Likely Failing Commands\n"
        + ("\n".join(f"- {command}" for command in likely_commands) if likely_commands else "- Unknown")
        + "\n\n"
        + "Affected Files\n"
        + ("\n".join(f"- {item['file']}" for item in list(effected_files_map.values())[:12]) if effected_files_map else "- Unknown")
        + "\n\n"
        + "Failed Jobs\n"
        + (
            "\n".join(
                f"- {job.get('step') or 'ci'}: {job.get('failed_command') or 'command not recovered'}"
                for job in list(failed_jobs_map.values())[:8]
            )
            if failed_jobs_map
            else "- Unknown"
        )
        + "\n\n"
        + "Failure Summary\n"
        + ("\n".join(f"- {reason}" for reason in overall_failure_reasons[:6]) if overall_failure_reasons else "- Unknown")
        + "\n\n"
        + "Mentioned Tokens\n"
        + ("\n".join(f"- {token}" for token in mentioned_tokens[:15]) if mentioned_tokens else "- None")
        + "\n\n"
        + "Workflow YAML\n"
        + str(row.get("workflow") or "")
        + "\n\n"
        + "Chunked Failure Summary\n"
        + (organized_log_summary or "No organized summary available.")
    )
    return {
        "overall_failure_reasons": overall_failure_reasons,
        "overall_error_types": base["overall_error_types"],
        "effected_files": list(effected_files_map.values()) or base["effected_files"],
        "failed_jobs": list(failed_jobs_map.values()) or base["failed_jobs"],
        "mentioned_tokens": mentioned_tokens or base["mentioned_tokens"],
        "organized_log_summary": organized_log_summary,
        "problem_document": problem_document,
        "chunk_outputs": chunk_outputs,
    }


def _extract_json_block(text: str) -> dict[str, Any]:
    try:
        payload = json.loads(text)
        if isinstance(payload, dict):
            return payload
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        raise ValueError("No JSON object found in model response")
    payload = json.loads(match.group(0))
    if not isinstance(payload, dict):
        raise ValueError("Model response JSON was not an object")
    return payload


def analyze_ci_artifacts_with_llm(
    row: dict[str, Any],
    *,
    model: str,
    api_base: str | None = None,
    api_key: str | None = None,
    temperature: float = 0.0,
    max_log_chars: int = 12000,
) -> dict[str, Any]:
    chunk_inputs = _chunk_log_entries(row, max_log_chars=max_log_chars)
    chunk_outputs: list[dict[str, Any]] = []
    for chunk in chunk_inputs:
        prompt = build_ci_log_chunk_prompt(row, chunk)
        response = litellm.completion(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
            api_base=api_base,
            api_key=api_key,
        )
        content = response.choices[0].message.content or ""
        parsed_chunk = _extract_json_block(content)
        heuristic_chunk = heuristic_ci_chunk_analysis(row, chunk)
        chunk_outputs.append(
            {
                "step_name": str(parsed_chunk.get("step_name") or chunk.get("step_name") or "ci"),
                "chunk_summary": str(parsed_chunk.get("chunk_summary") or "").strip() or heuristic_chunk["chunk_summary"],
                "mentioned_tokens": _normalize_text_list(parsed_chunk.get("mentioned_tokens")) or heuristic_chunk["mentioned_tokens"],
                "effected_files": _normalize_effected_files(parsed_chunk.get("effected_files")) or heuristic_chunk["effected_files"],
                "failed_jobs": _normalize_failed_jobs(parsed_chunk.get("failed_jobs")) or heuristic_chunk["failed_jobs"],
            }
        )

    merge_prompt = build_ci_merge_prompt(row, chunk_outputs)
    response = litellm.completion(
        model=model,
        messages=[{"role": "user", "content": merge_prompt}],
        temperature=temperature,
        api_base=api_base,
        api_key=api_key,
    )
    content = response.choices[0].message.content or ""
    parsed = _extract_json_block(content)
    heuristic = heuristic_ci_problem_document_from_chunks(row, chunk_outputs)
    return {
        "overall_failure_reasons": _normalize_text_list(parsed.get("overall_failure_reasons")) or heuristic["overall_failure_reasons"],
        "overall_error_types": _normalize_text_list(parsed.get("overall_error_types")) or heuristic["overall_error_types"],
        "effected_files": _normalize_effected_files(parsed.get("effected_files")) or heuristic["effected_files"],
        "failed_jobs": _normalize_failed_jobs(parsed.get("failed_jobs")) or heuristic["failed_jobs"],
        "mentioned_tokens": _normalize_text_list(parsed.get("mentioned_tokens")) or heuristic["mentioned_tokens"],
        "organized_log_summary": str(parsed.get("organized_log_summary") or "").strip() or heuristic["organized_log_summary"],
        "problem_document": str(parsed.get("problem_document") or "").strip() or heuristic["problem_document"],
        "chunk_outputs": heuristic.get("chunk_outputs", chunk_outputs),
    }
