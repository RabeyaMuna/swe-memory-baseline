from __future__ import annotations

import json
from collections import Counter, defaultdict
from math import sqrt
import os
from pathlib import Path
from typing import Any

from sweagent.benchmark.ci_repair_memory import (
    _extract_failed_commands,
    _infer_failed_tool,
    _overlap_ratio,
    _stable_repo_id,
    _tokenize,
    build_memory_record,
    normalize_logs,
)


L1_WEIGHT = 0.60
L2_WEIGHT = 0.30
L3_WEIGHT = 0.10
LEVEL_THRESHOLDS = {"L1": 0.40, "L2": 0.40, "L3": 0.50}  # Updated: L1 from 0.30 to 0.40
ABLATION_THRESHOLDS = {"L1": 0.55, "L1+L2": 0.37, "L1+L2+L3": 0.33}

# Component-based scoring weights (Signal breakdown per level)
# Each component contributes to the final similarity score
COMPONENT_WEIGHTS = {
    "L1": {  # File-level: exact matches most important
        "file_match": 0.35,      # Exact file match
        "error_match": 0.20,     # Error type match
        "pattern_similarity": 0.15,  # Failure pattern cosine similarity
        "tool_overlap": 0.10,    # Failed tool Jaccard overlap
        "reason_similarity": 0.20,   # Failure reason TF-IDF cosine similarity
    },
    "L2": {  # Repo-level: broader context matters
        "file_match": 0.0,       # Not applicable at repo level
        "error_match": 0.25,     # Error type match
        "pattern_similarity": 0.15,  # Failure pattern
        "tool_overlap": 0.15,    # Tool patterns
        "reason_similarity": 0.45,   # Failure reason most important
    },
    "L3": {  # Cross-repo: high quality evidence required
        "file_match": 0.0,       # Not applicable at cross-repo level
        "error_match": 0.20,     # Error type match
        "pattern_similarity": 0.15,  # Pattern consistency
        "tool_overlap": 0.10,    # Tool consistency
        "reason_similarity": 0.55,   # Failure reason text dominates
    },
}


def _normalize_str_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value or "").strip()
    return [text] if text else []


def _normalize_path(path: str) -> str:
    return (path or "").strip().lstrip("/").replace("\\", "/")


def _basename(path: str) -> str:
    return os.path.basename(_normalize_path(path))


def _repo_key(value: Any) -> str:
    text = str(value or "").strip().lower().strip("/")
    if "/" in text:
        return text.split("/")[-1]
    return text


def _repo_match(left: Any, right: Any) -> bool:
    left_text = str(left or "").strip().lower().strip("/")
    right_text = str(right or "").strip().lower().strip("/")
    if not left_text or not right_text:
        return False
    return left_text == right_text or _repo_key(left_text) == _repo_key(right_text)


def _active_levels(ablation_levels: str) -> set[str]:
    levels = {level.strip() for level in str(ablation_levels or "L1+L2+L3").split("+") if level.strip()}
    return levels or {"L1", "L2", "L3"}


def _level_weights(ablation_levels: str) -> dict[str, float]:
    active_levels = _active_levels(ablation_levels)
    base = {"L1": L1_WEIGHT, "L2": L2_WEIGHT, "L3": L3_WEIGHT}
    active_sum = sum(base[level] for level in active_levels if level in base)
    return {
        level: (base[level] / active_sum if level in active_levels and active_sum > 0 else 0.0)
        for level in ("L1", "L2", "L3")
    }


def _global_threshold(ablation_levels: str) -> float:
    return float(ABLATION_THRESHOLDS.get(str(ablation_levels or "L1+L2+L3"), 0.33))


def _first_text(value: Any) -> str:
    values = _normalize_str_list(value)
    return values[0] if values else ""


def _token_counter(text: str) -> Counter[str]:
    return Counter(_tokenize(text))


def _cosine_similarity(left_text: str, right_text: str) -> float:
    left = _token_counter(left_text)
    right = _token_counter(right_text)
    if not left or not right:
        return 0.0
    numerator = sum(left[token] * right[token] for token in left.keys() & right.keys())
    if numerator == 0:
        return 0.0
    left_norm = sqrt(sum(value * value for value in left.values()))
    right_norm = sqrt(sum(value * value for value in right.values()))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return numerator / (left_norm * right_norm)


def _jaccard_text_similarity(left_text: str, right_text: str) -> float:
    left_tokens = _tokenize(left_text)
    right_tokens = _tokenize(right_text)
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def _tfidf_cosine_similarity(left_text: str, right_text: str) -> float:
    """
    TF-IDF style similarity: emphasize rare/distinguishing tokens.
    Uses token frequency inverse document frequency weighting.
    """
    left_tokens = _tokenize(left_text)
    right_tokens = _tokenize(right_text)
    if not left_tokens or not right_tokens:
        return 0.0
    
    # Combine all tokens for IDF calculation
    all_tokens = left_tokens | right_tokens
    doc_freq = {}
    for token in all_tokens:
        doc_freq[token] = (1 if token in left_tokens else 0) + (1 if token in right_tokens else 0)
    
    # IDF weight (rarer tokens get higher weight)
    idf_weights = {token: 1.0 / doc_freq[token] for token in all_tokens if doc_freq[token] > 0}
    
    # Weighted TF vectors
    left_weighted = {token: idf_weights.get(token, 0) for token in left_tokens}
    right_weighted = {token: idf_weights.get(token, 0) for token in right_tokens}
    
    # Cosine similarity with IDF weights
    numerator = sum(
        left_weighted.get(token, 0) * right_weighted.get(token, 0)
        for token in left_tokens & right_tokens
    )
    if numerator == 0:
        return 0.0
    
    left_norm = sqrt(sum(v * v for v in left_weighted.values()))
    right_norm = sqrt(sum(v * v for v in right_weighted.values()))
    
    if left_norm == 0 or right_norm == 0:
        return 0.0
    
    return numerator / (left_norm * right_norm)


def _exact_match_score(query_val: Any, record_val: Any) -> float:
    """Return 1.0 for exact match, 0.0 otherwise."""
    if query_val is None or record_val is None:
        return 0.0
    return 1.0 if str(query_val).strip().lower() == str(record_val).strip().lower() else 0.0


def _component_based_similarity(
    query_context: dict[str, Any],
    record: dict[str, Any],
    level: str,
) -> float:
    """
    Compute component-based similarity score.
    
    Components:
    - file_match: Exact file path match (L1 only)
    - error_match: Error type overlap
    - pattern_similarity: Failure pattern cosine similarity
    - tool_overlap: Failed tool Jaccard overlap
    - reason_similarity: Failure reason TF-IDF cosine similarity
    
    Each level weights components differently.
    """
    if level not in COMPONENT_WEIGHTS:
        return 0.0
    
    weights = COMPONENT_WEIGHTS[level]
    component_scores = {}
    
    # File match (exact, L1 only)
    if weights["file_match"] > 0:
        query_files = set(f.lower().strip() for f in query_context.get("changed_files", []))
        record_file = str(record.get("file", "")).lower().strip()
        component_scores["file_match"] = 1.0 if record_file in query_files else 0.0
    else:
        component_scores["file_match"] = 0.0
    
    # Error type match
    if weights["error_match"] > 0:
        query_errors = set(str(e).lower() for e in query_context.get("error_types", []))
        record_errors = set(str(e).lower() for e in (record.get("error_types") or []))
        if query_errors and record_errors:
            component_scores["error_match"] = len(query_errors & record_errors) / len(query_errors | record_errors)
        else:
            component_scores["error_match"] = 0.0
    else:
        component_scores["error_match"] = 0.0
    
    # Pattern similarity (cosine on failure patterns)
    if weights["pattern_similarity"] > 0:
        query_patterns = " ".join(query_context.get("failure_patterns", []))
        record_patterns = " ".join(record.get("failure_pattern") or [])
        component_scores["pattern_similarity"] = _cosine_similarity(query_patterns, record_patterns)
    else:
        component_scores["pattern_similarity"] = 0.0
    
    # Tool overlap (Jaccard on failed tools)
    if weights["tool_overlap"] > 0:
        query_tools = set(str(t).lower() for t in [query_context.get("failed_tool", "")])
        record_tools = set(str(t).lower() for t in ([record.get("failed_tool", "")] if record.get("failed_tool") else []))
        if query_tools and record_tools:
            component_scores["tool_overlap"] = len(query_tools & record_tools) / len(query_tools | record_tools)
        else:
            component_scores["tool_overlap"] = 0.0
    else:
        component_scores["tool_overlap"] = 0.0
    
    # Reason similarity (TF-IDF on failure reason text)
    if weights["reason_similarity"] > 0:
        query_reason = query_context.get("failure_reason", "")
        record_reason = record.get("failure_reason", "")
        component_scores["reason_similarity"] = _tfidf_cosine_similarity(query_reason, record_reason)
    else:
        component_scores["reason_similarity"] = 0.0
    
    # Weighted sum
    final_score = sum(
        component_scores.get(comp, 0.0) * weight
        for comp, weight in weights.items()
    )
    
    return min(1.0, max(0.0, final_score))


def _effected_files(row: dict[str, Any]) -> list[dict[str, Any]]:
    ctx = row.get("ci_structured_context") if isinstance(row.get("ci_structured_context"), dict) else {}
    value = ctx.get("effected_files") if isinstance(ctx, dict) else []
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _current_issue_context(row: dict[str, Any]) -> dict[str, Any]:
    ci_structured_context = row.get("ci_structured_context") if isinstance(row.get("ci_structured_context"), dict) else {}
    logs = normalize_logs(row, max_chars=2000)
    changed_files = [str(f).strip() for f in (row.get("changed_files") or []) if str(f).strip()]
    error_types = row.get("error_type") or []
    if not isinstance(error_types, list):
        error_types = [str(error_types)]
    failed_jobs = ci_structured_context.get("failed_jobs") if isinstance(ci_structured_context, dict) else []
    if not isinstance(failed_jobs, list):
        failed_jobs = []
    failed_job_names = [
        str(item.get("step") or item.get("step_name") or "").strip()
        for item in failed_jobs
        if isinstance(item, dict) and str(item.get("step") or item.get("step_name") or "").strip()
    ]
    effected_files = _effected_files(row)
    affected_file_paths = [
        str(item.get("file") or "").strip()
        for item in effected_files
        if str(item.get("file") or "").strip()
    ]
    reasons = _normalize_str_list(ci_structured_context.get("overall_failure_reasons") if isinstance(ci_structured_context, dict) else [])
    mentioned_tokens = _normalize_str_list(ci_structured_context.get("mentioned_tokens") if isinstance(ci_structured_context, dict) else [])
    organized_log_summary = str(ci_structured_context.get("organized_log_summary") or "") if isinstance(ci_structured_context, dict) else ""
    current_commands = _extract_failed_commands(logs) or _normalize_str_list(row.get("failed_cmd"))
    failed_tool = _infer_failed_tool(current_commands, logs)
    if failed_tool == "unknown":
        failed_tool = (_normalize_str_list(row.get("failed_tool")) or ["unknown"])[0]
    failure_reason = " | ".join(reasons[:4]).strip() or organized_log_summary or logs
    failure_pattern = (
        _first_text(ci_structured_context.get("overall_error_types") if isinstance(ci_structured_context, dict) else [])
        or _first_text(row.get("error_type"))
        or _first_text(row.get("primary_error_type"))
        or _first_text(row.get("issue_type"))
    )
    retrieval_document = "\n".join(
        [
            f"repo: {_stable_repo_id(row)}",
            f"workflow: {str(row.get('workflow_name') or '').strip()} [{str(row.get('workflow_path') or '').strip()}]",
            f"error_types: {', '.join(str(x).strip() for x in error_types if str(x).strip())}",
            f"failed_jobs: {', '.join(failed_job_names)}",
            f"failed_commands: {' | '.join(current_commands[:4])}",
            f"affected_files: {', '.join(affected_file_paths[:6] or changed_files[:6])}",
            f"failure_reasons: {' '.join(reasons[:4])}",
            f"mentioned_tokens: {', '.join(mentioned_tokens[:20])}",
            f"log_summary: {organized_log_summary}",
            f"problem_document: {str(row.get('ci_problem_document') or '')[:1200]}",
        ]
    ).strip()
    return {
        "repo": _stable_repo_id(row),
        "workflow_name": str(row.get("workflow_name") or "").strip(),
        "workflow_path": str(row.get("workflow_path") or "").strip(),
        "error_types": [str(x).strip() for x in error_types if str(x).strip()],
        "changed_files": changed_files,
        "affected_files": affected_file_paths,
        "failed_jobs": failed_job_names,
        "failed_commands": current_commands,
        "failed_tool": failed_tool,
        "failure_pattern": failure_pattern,
        "failure_reason": failure_reason,
        "overall_failure_reasons": reasons,
        "mentioned_tokens": mentioned_tokens,
        "organized_log_summary": organized_log_summary,
        "retrieval_document": retrieval_document,
    }


def _record_error_types(row: dict[str, Any], file_info: dict[str, Any]) -> list[str]:
    issue_error_types = row.get("error_type") or []
    if not isinstance(issue_error_types, list):
        issue_error_types = [str(issue_error_types)]
    merged: list[str] = []
    for item in (
        _normalize_str_list(file_info.get("error_type"))
        + _normalize_str_list(file_info.get("issue_type"))
        + [str(x).strip() for x in issue_error_types if str(x).strip()]
    ):
        if item and item not in merged:
            merged.append(item)
    return merged


def _dependent_files_for(file_path: str, all_files: list[str], failure_reason: str) -> list[dict[str, str]]:
    dependents: list[dict[str, str]] = []
    for candidate in all_files:
        candidate = str(candidate).strip()
        if not candidate or candidate == file_path:
            continue
        dependents.append(
            {
                "file": candidate,
                "reason": (
                    f"Changed alongside {file_path}. "
                    + (failure_reason if failure_reason else "May participate in the same CI failure.")
                )[:240],
            }
        )
    return dependents[:5]


def _file_reason_text(base_record: dict[str, Any], file_info: dict[str, Any]) -> str:
    reason = str(file_info.get("reason") or "").strip()
    if reason:
        return reason
    overall = " ".join(_normalize_str_list(base_record.get("overall_failure_reasons"))[:2]).strip()
    if overall:
        return overall
    return str(base_record.get("organized_log_summary") or "")[:240]


def _build_l1_retrieval_document(record: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"repo: {record.get('repo', '')}",
            f"workflow: {record.get('workflow_name', '')} [{record.get('workflow_path', '')}]",
            f"file: {record.get('file', '')}",
            f"error_types: {', '.join(record.get('error_types') or [])}",
            f"failed_jobs: {', '.join(record.get('failed_jobs') or [])}",
            f"failed_commands: {' | '.join((record.get('failed_commands') or [])[:4])}",
            f"validation_cmd: {record.get('validation_cmd', '')}",
            f"failure_reason: {record.get('failure_reason', '')}",
            f"failure_pattern: {' | '.join(record.get('failure_pattern') or [])}",
            f"fix_pattern: {' | '.join(record.get('fix_pattern') or [])}",
            "dependent_files: "
            + ", ".join(
                f"{item.get('file', '')}:{item.get('reason', '')}"
                for item in (record.get("dependent_files") or [])
                if isinstance(item, dict)
            ),
            f"mentioned_tokens: {', '.join(record.get('mentioned_tokens') or [])}",
            f"log_summary: {record.get('organized_log_summary', '')}",
            f"fix_summary: {record.get('fix_summary', '')}",
        ]
    ).strip()


def build_l1_memory(seed_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records = []
    for row in seed_rows:
        base = build_memory_record(row)
        failed_jobs = row.get("ci_structured_context", {}).get("failed_jobs", []) if isinstance(row.get("ci_structured_context"), dict) else []
        failed_job_names = [
            str(item.get("step") or item.get("step_name") or "").strip()
            for item in failed_jobs
            if isinstance(item, dict) and str(item.get("step") or item.get("step_name") or "").strip()
        ]
        effected_files = _effected_files(row)
        candidate_files: list[str] = []
        for source in (
            effected_files,
            [{"file": path} for path in (base.get("likely_target_files") or [])],
            [{"file": path} for path in (base.get("changed_files") or [])],
        ):
            for item in source:
                cleaned = str(item.get("file") or "").strip()
                if cleaned and cleaned not in candidate_files:
                    candidate_files.append(cleaned)
        if not candidate_files:
            candidate_files = ["unknown_file"]

        for file_path in candidate_files:
            file_info = next((item for item in effected_files if str(item.get("file") or "").strip() == file_path), {})
            failure_reason = _file_reason_text(base, file_info)
            failure_pattern = _normalize_str_list(file_info.get("error_subtype")) or list(base.get("failure_patterns") or [])
            fix_pattern = list(base.get("patch_patterns") or [])
            validation_cmd = str(file_info.get("failed_command") or "").strip() or str(
                (base.get("validation_commands") or [""])[0]
            ).strip()
            record = {
                "memory_level": "L1",
                "memory_id": f"{base['memory_id']}::{file_path}",
                "issue_memory_id": base["memory_id"],
                "repo": base["repo"],
                "workflow_name": base["workflow_name"],
                "workflow_path": base["workflow_path"],
                "sha_fail": base["sha_fail"],
                "file": file_path,
                "error_types": _record_error_types(row, file_info),
                "failure_pattern": failure_pattern,
                "failed_commands": list(base.get("failed_commands") or []),
                "failed_jobs": failed_job_names,
                "failed_tool": base.get("failed_tool", "unknown"),
                "fix_pattern": fix_pattern,
                "validation_cmd": validation_cmd,
                "failure_reason": failure_reason,
                "dependent_files": _dependent_files_for(file_path, candidate_files, failure_reason),
                "mentioned_tokens": list(base.get("mentioned_tokens") or []),
                "organized_log_summary": base.get("organized_log_summary", ""),
                "overall_failure_reasons": list(base.get("overall_failure_reasons") or []),
                "fix_summary": (
                    f"File-level fix for {file_path}. "
                    f"Patch styles: {', '.join(fix_pattern[:4])}. "
                    f"Validation command: {validation_cmd or 'unknown'}."
                ).strip(),
            }
            record["retrieval_document"] = _build_l1_retrieval_document(record)
            records.append(record)
    return records


def build_l2_memory(l1_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in l1_records:
        repo = str(record.get("repo") or "")
        workflow_name = str(record.get("workflow_name") or "")
        for error_type in record.get("error_types") or ["Unknown"]:
            grouped[(repo, str(error_type), workflow_name)].append(record)

    l2_records: list[dict[str, Any]] = []
    for (repo, error_type, workflow_name), rows in grouped.items():
        files: list[dict[str, str]] = []
        failure_patterns: list[str] = []
        fix_patterns: list[str] = []
        failed_commands: list[str] = []
        failed_tools: list[str] = []
        failed_jobs: list[str] = []
        mentioned_tokens: list[str] = []
        reasons: list[str] = []
        for row in rows:
            file_path = str(row.get("file") or "").strip()
            failure_reason = str(row.get("failure_reason") or "").strip()
            if file_path and not any(item["file"] == file_path for item in files):
                files.append({"file": file_path, "reason": failure_reason})
            for key, bucket in (
                ("failure_pattern", failure_patterns),
                ("fix_pattern", fix_patterns),
                ("failed_commands", failed_commands),
                ("failed_tool", failed_tools),
                ("failed_jobs", failed_jobs),
                ("mentioned_tokens", mentioned_tokens),
            ):
                for item in row.get(key) or []:
                    text = str(item).strip()
                    if text and text not in bucket:
                        bucket.append(text)
            if failure_reason and failure_reason not in reasons:
                reasons.append(failure_reason)
        retrieval_document = "\n".join(
            [
                f"repo: {repo}",
                f"workflow: {workflow_name}",
                f"error_type: {error_type}",
                f"common_failed_jobs: {', '.join(failed_jobs[:8])}",
                f"common_failed_commands: {' | '.join(failed_commands[:5])}",
                f"common_files: {', '.join(item['file'] for item in files[:8])}",
                f"failure_reasons: {' '.join(reasons[:5])}",
                f"mentioned_tokens: {', '.join(mentioned_tokens[:20])}",
                f"failure_patterns: {', '.join(failure_patterns[:8])}",
                f"fix_patterns: {', '.join(fix_patterns[:8])}",
            ]
        ).strip()
        l2_records.append(
            {
                "memory_level": "L2",
                "repo": repo,
                "workflow_name": workflow_name,
                "error_type": error_type,
                "source_count": len(rows),
                "files": files[:10],
                "failed_commands": failed_commands,
                "failed_tool": failed_tools,
                "failed_jobs": failed_jobs,
                "failure_patterns": failure_patterns,
                "fix_patterns": fix_patterns,
                "reasons": reasons,
                "mentioned_tokens": mentioned_tokens,
                "retrieval_document": retrieval_document,
                "fix_summary": (
                    f"Repo-level recurring pattern for {repo} / {error_type}. "
                    f"Common files: {', '.join(item['file'] for item in files[:5])}. "
                    f"Typical commands: {' | '.join(failed_commands[:3])}."
                ).strip(),
            }
        )
    return l2_records


def build_l3_memory(l1_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in l1_records:
        failed_tool = str(record.get("failed_tool") or "unknown")
        for error_type in record.get("error_types") or ["Unknown"]:
            grouped[(str(error_type), failed_tool)].append(record)

    l3_records: list[dict[str, Any]] = []
    for (error_type, failed_tool), rows in grouped.items():
        failure_patterns: list[str] = []
        fix_patterns: list[str] = []
        failed_tools: list[str] = []
        failed_jobs: list[str] = []
        mentioned_tokens: list[str] = []
        reasons: list[str] = []
        repos: list[str] = []
        for row in rows:
            repo = str(row.get("repo") or "").strip()
            if repo and repo not in repos:
                repos.append(repo)
            for key, bucket in (
                ("failure_pattern", failure_patterns),
                ("fix_pattern", fix_patterns),
                ("failed_tool", failed_tools),
                ("failed_jobs", failed_jobs),
                ("mentioned_tokens", mentioned_tokens),
            ):
                for item in row.get(key) or []:
                    text = str(item).strip()
                    if text and text not in bucket:
                        bucket.append(text)
            reason = str(row.get("failure_reason") or "").strip()
            if reason and reason not in reasons:
                reasons.append(reason)
        retrieval_document = "\n".join(
            [
                f"error_type: {error_type}",
                f"failed_tool: {failed_tool}",
                f"repos: {', '.join(repos[:10])}",
                f"common_failed_jobs: {', '.join(failed_jobs[:8])}",
                f"failure_reasons: {' '.join(reasons[:5])}",
                f"mentioned_tokens: {', '.join(mentioned_tokens[:20])}",
                f"failure_patterns: {', '.join(failure_patterns[:8])}",
                f"fix_patterns: {', '.join(fix_patterns[:8])}",
            ]
        ).strip()
        l3_records.append(
            {
                "memory_level": "L3",
                "error_type": error_type,
                "failed_tool": failed_tool,
                "source_count": len(rows),
                "repos": repos,
                "failure_patterns": failure_patterns,
                "fix_patterns": fix_patterns,
                "failed_tool_list": failed_tools,
                "failed_jobs": failed_jobs,
                "mentioned_tokens": mentioned_tokens,
                "reasons": reasons,
                "retrieval_document": retrieval_document,
                "fix_summary": (
                    f"Cross-repo pattern for {error_type} with tool {failed_tool}. "
                    f"Typical patterns: {', '.join(failure_patterns[:4])}. "
                    f"Typical patch styles: {', '.join(fix_patterns[:4])}."
                ).strip(),
            }
        )
    return l3_records


def build_hierarchical_memory_bank(seed_rows: list[dict[str, Any]]) -> dict[str, Any]:
    l1 = build_l1_memory(seed_rows)
    return {"l1": l1, "l2": build_l2_memory(l1), "l3": build_l3_memory(l1)}


def _external_l1_to_internal(record: dict[str, Any]) -> dict[str, Any]:
    dependent_files = [
        item if isinstance(item, dict) else {"file": str(item).strip(), "reason": ""}
        for item in (record.get("dependent_files") or [])
        if (isinstance(item, dict) and str(item.get("file") or "").strip()) or str(item).strip()
    ]
    failed_tool = _normalize_str_list(record.get("failed_tool"))
    failure_pattern = _normalize_str_list(record.get("failure_pattern"))
    fix_pattern = _normalize_str_list(record.get("fix_pattern"))
    failed_commands = _normalize_str_list(record.get("failed_cmd"))
    repo = str(record.get("repo_full_name") or record.get("repo") or record.get("repo_name") or "").strip()
    workflow_name = str(record.get("workflow_name") or "").strip()
    workflow_path = str(record.get("workflow_path") or "").strip()
    failure_reason = str(record.get("failure_reason") or record.get("reason") or "").strip()
    item = {
        "memory_level": "L1",
        "memory_id": f"{repo or 'unknown'}::{record.get('issue_id', '')}::{record.get('file', '')}",
        "issue_memory_id": f"{repo or 'unknown'}-{record.get('issue_id', '')}",
        "repo": repo,
        "workflow_name": workflow_name,
        "workflow_path": workflow_path,
        "sha_fail": str(record.get("sha_fail") or "").strip(),
        "file": str(record.get("file") or "").strip(),
        "error_types": _normalize_str_list(record.get("error_type")),
        "failure_pattern": failure_pattern,
        "failed_commands": failed_commands,
        "failed_jobs": [],
        "failed_tool": failed_tool[0] if failed_tool else "unknown",
        "fix_pattern": fix_pattern,
        "validation_cmd": failed_commands[0] if failed_commands else "",
        "failure_reason": failure_reason,
        "dependent_files": dependent_files,
        "mentioned_tokens": failed_tool + failure_pattern + fix_pattern,
        "organized_log_summary": failure_reason,
        "overall_failure_reasons": [failure_reason] if failure_reason else [],
        "fix_summary": str(record.get("fix_strategy") or "").strip(),
    }
    item["retrieval_document"] = _build_l1_retrieval_document(item)
    return item


def _external_l2_to_internal(record: dict[str, Any]) -> dict[str, Any]:
    failed_commands = _normalize_str_list(record.get("failed_cmd"))
    failed_tool = _normalize_str_list(record.get("failed_tool"))
    failure_patterns = _normalize_str_list(record.get("failure_pattern"))
    fix_patterns = _normalize_str_list(record.get("fix_pattern"))
    reasons = _normalize_str_list(record.get("failure_reason"))
    files = [
        {
            "file": str(item.get("file") or "").strip(),
            "reason": str(item.get("failure_reason") or item.get("reason") or "").strip(),
        }
        for item in (record.get("files") or [])
        if isinstance(item, dict) and str(item.get("file") or "").strip()
    ]
    repo = str(record.get("repo_full_name") or record.get("repo") or record.get("repo_name") or "").strip()
    workflow_name = str(record.get("workflow_name") or "").strip()
    workflow_path = str(record.get("workflow_path") or "").strip()
    retrieval_document = "\n".join(
        [
            f"repo: {repo}",
            f"workflow: {workflow_name} [{workflow_path}]",
            f"error_type: {str(record.get('error_type') or '').strip()}",
            f"common_failed_commands: {' | '.join(failed_commands[:5])}",
            f"common_files: {', '.join(item['file'] for item in files[:8])}",
            f"failure_reasons: {' '.join(reasons[:5])}",
            f"failure_patterns: {', '.join(failure_patterns[:8])}",
            f"fix_patterns: {', '.join(fix_patterns[:8])}",
        ]
    ).strip()
    return {
        "memory_level": "L2",
        "repo": repo,
        "workflow_name": workflow_name,
        "workflow_path": workflow_path,
        "error_type": str(record.get("error_type") or "").strip(),
        "source_count": len(_normalize_str_list(record.get("issue_ids"))) or 1,
        "files": files,
        "failed_commands": failed_commands,
        "failed_tool": failed_tool,
        "failed_jobs": [],
        "failure_patterns": failure_patterns,
        "fix_patterns": fix_patterns,
        "reasons": reasons,
        "mentioned_tokens": failed_tool + failure_patterns + fix_patterns,
        "retrieval_document": retrieval_document,
        "fix_summary": str(record.get("fix_strategy") or "").strip(),
    }


def _external_l3_to_internal(record: dict[str, Any]) -> dict[str, Any]:
    failed_tool = _normalize_str_list(record.get("failed_tool"))
    failure_patterns = _normalize_str_list(record.get("failure_patterns") or record.get("failure_pattern"))
    fix_patterns = _normalize_str_list(record.get("fix_pattern"))
    reasons = _normalize_str_list(record.get("failure_reasons") or record.get("failure_reason"))
    repos = _normalize_str_list(record.get("repos") or record.get("repo"))
    retrieval_document = "\n".join(
        [
            f"error_type: {str(record.get('error_type') or '').strip()}",
            f"failed_tool: {failed_tool[0] if failed_tool else 'unknown'}",
            f"repos: {', '.join(repos[:10])}",
            f"failure_reasons: {' '.join(reasons[:5])}",
            f"failure_patterns: {', '.join(failure_patterns[:8])}",
            f"fix_patterns: {', '.join(fix_patterns[:8])}",
        ]
    ).strip()
    return {
        "memory_level": "L3",
        "error_type": str(record.get("error_type") or "").strip(),
        "failed_tool": failed_tool[0] if failed_tool else "unknown",
        "source_count": 1,
        "repos": repos,
        "failure_patterns": failure_patterns,
        "fix_patterns": fix_patterns,
        "failed_tool_list": failed_tool,
        "failed_jobs": [],
        "mentioned_tokens": failed_tool + failure_patterns + fix_patterns,
        "reasons": reasons,
        "retrieval_document": retrieval_document,
        "fix_summary": str(record.get("principle") or record.get("fix_strategy") or "").strip(),
    }


def load_memory_bank(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    if path.is_dir() or path.name in {"memory_bank_summary.json", "failure_memory.json", "repo_memory.json", "cross_memory.json"}:
        base_dir = path if path.is_dir() else path.parent
        failure_path = base_dir / "failure_memory.json"
        repo_path = base_dir / "repo_memory.json"
        cross_path = base_dir / "cross_memory.json"
        if failure_path.exists() and repo_path.exists() and cross_path.exists():
            return {
                "l1": [_external_l1_to_internal(item) for item in json.loads(failure_path.read_text())],
                "l2": [_external_l2_to_internal(item) for item in json.loads(repo_path.read_text())],
                "l3": [_external_l3_to_internal(item) for item in json.loads(cross_path.read_text())],
            }
    data = json.loads(path.read_text())
    if isinstance(data, dict) and {"l1", "l2", "l3"} & set(data):
        return data
    msg = f"Unsupported memory bank format: {path}"
    raise ValueError(msg)


def summarize_hierarchical_memory_bank(
    memory_bank: dict[str, Any],
    *,
    seed_file: str | None = None,
    analysis_file: str | None = None,
    model_key: str | None = None,
) -> dict[str, Any]:
    return {
        "model_key": model_key or "heuristic",
        "seed_file": seed_file or "",
        "analysis_file": analysis_file or "",
        "total_issues": len({str(item.get("issue_memory_id") or "") for item in (memory_bank.get("l1") or []) if str(item.get("issue_memory_id") or "")}),
        "processed": len({str(item.get("issue_memory_id") or "") for item in (memory_bank.get("l1") or []) if str(item.get("issue_memory_id") or "")}),
        "skipped": 0,
        "memory_counts": {
            "L1_failure_memory": len(memory_bank.get("l1") or []),
            "L2_repo_memory": len(memory_bank.get("l2") or []),
            "L3_cross_memory": len(memory_bank.get("l3") or []),
        },
    }


def _workflow_similarity(current: dict[str, Any], record: dict[str, Any]) -> float:
    workflow_name_match = (
        str(current.get("workflow_name") or "").strip().lower()
        == str(record.get("workflow_name") or "").strip().lower()
    )
    workflow_path_match = (
        str(current.get("workflow_path") or "").strip().lower()
        == str(record.get("workflow_path") or "").strip().lower()
    )
    score = 0.0
    if workflow_name_match:
        score += 0.6
    if workflow_path_match:
        score += 0.4
    return min(score, 1.0)


def _failure_reason_similarity(current: dict[str, Any], record: dict[str, Any]) -> float:
    current_text = " ".join(current.get("overall_failure_reasons") or []) or str(current.get("organized_log_summary") or "")
    record_text = str(record.get("failure_reason") or "") or " ".join(record.get("reasons") or []) or str(record.get("organized_log_summary") or "")
    return _jaccard_text_similarity(current_text, record_text)


def _file_pattern_similarity(current: dict[str, Any], record: dict[str, Any]) -> float:
    current_files = current.get("affected_files") or current.get("changed_files") or []
    record_files: list[str] = []
    if str(record.get("file") or "").strip():
        record_files.append(str(record.get("file") or "").strip())
    for item in record.get("dependent_files") or []:
        if isinstance(item, dict) and str(item.get("file") or "").strip():
            record_files.append(str(item.get("file") or "").strip())
    if not record_files:
        for item in record.get("files") or []:
            if isinstance(item, dict) and str(item.get("file") or "").strip():
                record_files.append(str(item.get("file") or "").strip())
    return _overlap_ratio(current_files, record_files)


def _failed_job_similarity(current: dict[str, Any], record: dict[str, Any]) -> float:
    return _overlap_ratio(current.get("failed_jobs") or [], record.get("failed_jobs") or [])


def _error_type_similarity(current: dict[str, Any], record: dict[str, Any]) -> float:
    return _overlap_ratio(current.get("error_types") or [], record.get("error_types") or [record.get("error_type") or ""])


def _text_similarity(current: dict[str, Any], record: dict[str, Any]) -> float:
    current_text = " ".join(
        _normalize_str_list(current.get("overall_failure_reasons")) + _normalize_str_list(current.get("mentioned_tokens"))
    )
    record_text = " ".join(
        _normalize_str_list(record.get("overall_failure_reasons"))
        + _normalize_str_list(record.get("mentioned_tokens"))
        + _normalize_str_list(record.get("reasons"))
    )
    if not current_text:
        current_text = str(current.get("organized_log_summary") or "")
    if not record_text:
        record_text = str(record.get("organized_log_summary") or "")
    return _jaccard_text_similarity(current_text, record_text)


def _annotate_match(record: dict[str, Any], *, score: float, score_breakdown: dict[str, float], rationale: str) -> dict[str, Any]:
    return {
        **record,
        "similarity_score": round(min(score, 1.0), 4),
        "score_breakdown": {key: round(value, 4) for key, value in score_breakdown.items()},
        "match_rationale": rationale,
    }


def _score_l1(current: dict[str, Any], record: dict[str, Any]) -> dict[str, Any] | None:
    if not _repo_match(current["repo"], record.get("repo")):
        return None
    query_files = list(current.get("affected_files") or []) + list(current.get("changed_files") or [])
    normalized_query_files = {_normalize_path(path) for path in query_files if str(path).strip()}
    query_basenames = {_basename(path) for path in normalized_query_files}
    row_file = _normalize_path(str(record.get("file") or ""))
    row_base = _basename(row_file)
    if row_file and row_file in normalized_query_files:
        file_score = 1.0
    elif row_base and row_base in query_basenames:
        file_score = 0.7
    else:
        file_score = 0.0
    row_error = _first_text(record.get("error_types")).lower()
    current_error = _first_text(current.get("error_types")).lower()
    error_score = 1.0 if current_error and row_error and current_error == row_error else 0.0
    row_pattern = _first_text(record.get("failure_pattern")).lower()
    current_pattern = str(current.get("failure_pattern") or "").lower()
    pattern_score = (
        1.0
        if current_pattern and row_pattern and current_pattern == row_pattern
        else _cosine_similarity(current_pattern, row_pattern)
    )
    query_tools = [str(current.get("failed_tool") or "").lower()] if str(current.get("failed_tool") or "").strip() else []
    row_tools = [str(record.get("failed_tool") or "").lower()] if str(record.get("failed_tool") or "").strip() else []
    tool_score = _overlap_ratio(query_tools, row_tools)
    query_doc = " ".join(
        [
            _first_text(current.get("error_types")),
            str(current.get("failure_pattern") or ""),
            str(current.get("failure_reason") or ""),
            " ".join(query_tools),
            " ".join(query_files),
        ]
    )
    row_doc = " ".join(
        [
            _first_text(record.get("error_types")),
            _first_text(record.get("failure_pattern")),
            str(record.get("failure_reason") or ""),
            " ".join(row_tools),
            row_file,
        ]
    )
    text_score = _cosine_similarity(query_doc, row_doc)
    score_breakdown = {
        "file_score": 0.35 * file_score,
        "error_score": 0.20 * error_score,
        "pattern_score": 0.15 * pattern_score,
        "tool_score": 0.10 * tool_score,
        "text_score": 0.20 * text_score,
    }
    similarity = round(sum(score_breakdown.values()), 4)
    if similarity < LEVEL_THRESHOLDS["L1"]:
        return None
    return _annotate_match(
        record,
        score=similarity,
        score_breakdown=score_breakdown,
        rationale="L1 direct match uses file, error type, failure pattern, tool, and text similarity with the external baseline weights.",
    )


def _score_l2(current: dict[str, Any], record: dict[str, Any]) -> dict[str, Any] | None:
    if not _repo_match(current["repo"], record.get("repo")):
        return None
    row_error = str(record.get("error_type") or _first_text(record.get("error_types"))).lower()
    current_error = _first_text(current.get("error_types")).lower()
    error_score = 1.0 if current_error and row_error and current_error == row_error else 0.0
    row_pattern = _first_text(record.get("failure_patterns") or record.get("failure_pattern")).lower()
    current_pattern = str(current.get("failure_pattern") or "").lower()
    pattern_score = (
        1.0
        if current_pattern and row_pattern and current_pattern == row_pattern
        else _cosine_similarity(current_pattern, row_pattern)
    )
    query_tools = [str(current.get("failed_tool") or "").lower()] if str(current.get("failed_tool") or "").strip() else []
    row_tools = [str(item).lower() for item in _normalize_str_list(record.get("failed_tool"))]
    if not row_tools:
        row_tools = [str(item).lower() for item in _normalize_str_list(record.get("failed_commands"))]
    tool_score = _overlap_ratio(query_tools, row_tools)
    query_doc = " ".join(
        [
            _first_text(current.get("error_types")),
            str(current.get("failure_pattern") or ""),
            str(current.get("failure_reason") or ""),
            " ".join(query_tools),
            " ".join(list(current.get("affected_files") or []) + list(current.get("changed_files") or [])),
        ]
    )
    row_doc = " ".join(
        [
            str(record.get("error_type") or ""),
            row_pattern,
            " ".join(_normalize_str_list(record.get("reasons"))),
            " ".join(_normalize_str_list(record.get("fix_patterns"))),
            " ".join(row_tools),
            " ".join(
                str(item.get("file") or "")
                for item in (record.get("files") or [])
                if isinstance(item, dict)
            ),
        ]
    )
    text_score = _cosine_similarity(query_doc, row_doc)
    score_breakdown = {
        "text_score": 0.45 * text_score,
        "error_score": 0.25 * error_score,
        "pattern_score": 0.15 * pattern_score,
        "tool_score": 0.15 * tool_score,
    }
    similarity = round(sum(score_breakdown.values()), 4)
    if similarity < LEVEL_THRESHOLDS["L2"]:
        return None
    return _annotate_match(
        record,
        score=similarity,
        score_breakdown=score_breakdown,
        rationale="L2 repo-level match uses the external baseline text, error type, pattern, and tool weights.",
    )


def _score_l3(current: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    row_error = str(record.get("error_type") or _first_text(record.get("error_types"))).lower()
    current_error = _first_text(current.get("error_types")).lower()
    error_type_match = 1.0 if current_error and row_error and current_error == row_error else 0.0
    row_pattern = _first_text(record.get("failure_patterns") or record.get("failure_pattern")).lower()
    current_pattern = str(current.get("failure_pattern") or "").lower()
    pattern_score = (
        1.0
        if current_pattern and row_pattern and current_pattern == row_pattern
        else _cosine_similarity(current_pattern, row_pattern)
    )
    query_tools = [str(current.get("failed_tool") or "").lower()] if str(current.get("failed_tool") or "").strip() else []
    row_tools = [str(item).lower() for item in _normalize_str_list(record.get("failed_tool_list") or record.get("failed_tool"))]
    tool_similarity = _overlap_ratio(query_tools, row_tools)
    query_doc = " ".join(
        [
            _first_text(current.get("error_types")),
            str(current.get("failure_pattern") or ""),
            str(current.get("failure_reason") or ""),
            " ".join(query_tools),
        ]
    )
    row_doc = " ".join(
        [
            str(record.get("error_type") or ""),
            row_pattern,
            " ".join(_normalize_str_list(record.get("reasons"))),
            " ".join(_normalize_str_list(record.get("fix_patterns"))),
            " ".join(row_tools),
        ]
    )
    text_similarity = _cosine_similarity(query_doc, row_doc)
    score_breakdown = {
        "text_score": 0.55 * text_similarity,
        "error_type_match": 0.20 * error_type_match,
        "pattern_score": 0.15 * pattern_score,
        "tool_similarity": 0.10 * tool_similarity,
    }
    similarity = round(sum(score_breakdown.values()), 4)
    if similarity < LEVEL_THRESHOLDS["L3"]:
        return None
    return _annotate_match(
        record,
        score=similarity,
        score_breakdown=score_breakdown,
        rationale="L3 cross-repo match uses the external baseline text, error type, pattern, and tool weights.",
    )


def retrieve_hierarchical_memory(
    row: dict[str, Any],
    memory_bank: dict[str, Any],
    *,
    top_k_l1: int = 3,
    ablation_levels: str = "L1+L2+L3",
) -> dict[str, Any]:
    current = _current_issue_context(row)
    active_levels = _active_levels(ablation_levels)
    level_weights = _level_weights(ablation_levels)
    similarity_threshold = _global_threshold(ablation_levels)

    l1_matches: list[dict[str, Any]] = []
    issue_id = f"{row.get('repo_owner', '')}__{row.get('repo_name', '')}-{row.get('id', '')}"
    if "L1" in active_levels:
        for record in memory_bank.get("l1") or []:
            if str(record.get("issue_memory_id") or "") == issue_id:
                continue
            match = _score_l1(current, record)
            if match:
                match["memory_level"] = "L1"
                l1_matches.append(match)
        l1_matches.sort(key=lambda item: float(item.get("similarity_score") or 0.0), reverse=True)
        l1_matches = l1_matches[:top_k_l1]

    l2_matches: list[dict[str, Any]] = []
    if "L2" in active_levels:
        for record in memory_bank.get("l2") or []:
            match = _score_l2(current, record)
            if match:
                match["memory_level"] = "L2"
                l2_matches.append(match)
        l2_matches.sort(key=lambda item: float(item.get("similarity_score") or 0.0), reverse=True)
        l2_matches = l2_matches[:top_k_l1]

    l3_matches: list[dict[str, Any]] = []
    if "L3" in active_levels:
        for record in memory_bank.get("l3") or []:
            match = _score_l3(current, record)
            if match:
                match["memory_level"] = "L3"
                l3_matches.append(match)
        l3_matches.sort(key=lambda item: float(item.get("similarity_score") or 0.0), reverse=True)
        l3_matches = l3_matches[:top_k_l1]

    level_scores = {
        "L1": round(max((float(item.get("similarity_score") or 0.0) for item in l1_matches), default=0.0), 4),
        "L2": round(max((float(item.get("similarity_score") or 0.0) for item in l2_matches), default=0.0), 4),
        "L3": round(max((float(item.get("similarity_score") or 0.0) for item in l3_matches), default=0.0), 4),
    }
    weighted_similarity = round(
        sum(level_weights[level] * level_scores[level] for level in ("L1", "L2", "L3")),
        4,
    )
    selected_levels = [
        level
        for level, rows in (("L1", l1_matches), ("L2", l2_matches), ("L3", l3_matches))
        if rows and level_scores.get(level, 0.0) >= LEVEL_THRESHOLDS[level]
    ]

    candidate_files: list[str] = []
    for item in l1_matches:
        path = _normalize_path(str(item.get("file") or ""))
        if path and path not in candidate_files:
            candidate_files.append(path)
    for item in l2_matches:
        for file_row in (item.get("files") or [])[:5]:
            if isinstance(file_row, dict):
                path = _normalize_path(str(file_row.get("file") or ""))
                if path and path not in candidate_files:
                    candidate_files.append(path)

    high_level_hints: list[str] = []
    for item in l2_matches:
        reason = _first_text(item.get("reasons"))
        if reason:
            high_level_hints.append(reason[:220])
    for item in l3_matches:
        principle = str(item.get("fix_summary") or "").strip()
        if principle:
            high_level_hints.append(principle[:220])

    use_memory = weighted_similarity >= similarity_threshold
    return {
        "current_issue_context": current,
        "thresholds": {"similarity_threshold": similarity_threshold, **LEVEL_THRESHOLDS},
        "weights": level_weights,
        "l1_candidates": l1_matches,
        "l2_candidate": l2_matches[0] if l2_matches else None,
        "l3_candidate": l3_matches[0] if l3_matches else None,
        "l1_matches": l1_matches,
        "l2_matches": l2_matches,
        "l3_matches": l3_matches,
        "level_scores": level_scores,
        "weighted_similarity": weighted_similarity,
        "selected_memory_levels": selected_levels,
        "llm_similarity_candidates": [*l1_matches[:3], *l2_matches[:1], *l3_matches[:1]],
        "candidate_files": candidate_files[:10],
        "high_level_hints": high_level_hints[:6],
        "matches": [*l1_matches, *l2_matches, *l3_matches],
        "reason": "" if use_memory else "below_weighted_threshold",
        "use_memory": use_memory,
        "enabled": bool(memory_bank),
        "ablation_levels": ablation_levels,
    }


def render_hierarchical_memory_context(result: dict[str, Any]) -> str:
    if not result.get("use_memory"):
        return ""
    current = result.get("current_issue_context") or {}
    thresholds = result.get("thresholds") or {}
    weights = result.get("weights") or {}
    lines = [
        "Retrieved prior CI repair memory. Use it as non-binding prior experience only.",
        f"Ablation levels: {result.get('ablation_levels', 'L1+L2+L3')}",
        f"Weighted similarity: {float(result.get('weighted_similarity') or 0.0):.2f}",
        "Weights: "
        + ", ".join(f"{level}={float(weights.get(level) or 0.0):.3f}" for level in ("L1", "L2", "L3")),
        "Thresholds: "
        + ", ".join(
            [
                f"weighted={float(thresholds.get('similarity_threshold') or 0.0):.2f}",
                f"L1={float(thresholds.get('L1') or 0.0):.2f}",
                f"L2={float(thresholds.get('L2') or 0.0):.2f}",
                f"L3={float(thresholds.get('L3') or 0.0):.2f}",
            ]
        ),
        "Current issue summary:",
        f"- Repo: {current.get('repo', '')}",
        f"- Workflow: {current.get('workflow_name', '')} [{current.get('workflow_path', '')}]",
        f"- Error types: {', '.join(current.get('error_types') or [])}",
        f"- Failed jobs: {', '.join(current.get('failed_jobs') or [])}",
        f"- Affected files: {', '.join((current.get('affected_files') or current.get('changed_files') or [])[:6])}",
    ]
    for candidate in result.get("l1_matches") or []:
        dependent_files = ", ".join(
            str(item.get("file") or "")
            for item in (candidate.get("dependent_files") or [])
            if isinstance(item, dict) and str(item.get("file") or "")
        )
        lines.extend(
            [
                "",
                f"L1 Prior Case (score={float(candidate.get('similarity_score') or 0.0):.2f})",
                f"- Repo: {candidate.get('repo', '')}",
                f"- Workflow: {candidate.get('workflow_name', '')}",
                f"- File: {candidate.get('file', '')}",
                f"- Failure reason: {candidate.get('failure_reason', '')}",
                f"- Failure pattern: {', '.join(candidate.get('failure_pattern') or [])}",
                f"- Failed jobs: {', '.join(candidate.get('failed_jobs') or [])}",
                f"- Validation command: {candidate.get('validation_cmd', '')}",
                f"- Dependent files: {dependent_files}",
                f"- Match rationale: {candidate.get('match_rationale', '')}",
                f"- Score breakdown: {candidate.get('score_breakdown', {})}",
                f"- Fix summary: {candidate.get('fix_summary', '')}",
            ]
        )
    for l2 in result.get("l2_matches") or []:
        lines.extend(
            [
                "",
                f"L2 Repo Pattern (score={float(l2.get('similarity_score') or 0.0):.2f})",
                f"- Repo: {l2.get('repo', '')}",
                f"- Error type: {l2.get('error_type', '')}",
                f"- Common files: {', '.join(item.get('file', '') for item in (l2.get('files') or []) if isinstance(item, dict))}",
                f"- Common commands: {' | '.join((l2.get('failed_commands') or [])[:3])}",
                f"- Match rationale: {l2.get('match_rationale', '')}",
                f"- Score breakdown: {l2.get('score_breakdown', {})}",
                f"- Fix summary: {l2.get('fix_summary', '')}",
            ]
        )
    for l3 in result.get("l3_matches") or []:
        lines.extend(
            [
                "",
                f"L3 General Pattern (score={float(l3.get('similarity_score') or 0.0):.2f})",
                f"- Error type: {l3.get('error_type', '')}",
                f"- Failed tool: {l3.get('failed_tool', '')}",
                f"- Common patterns: {', '.join(l3.get('failure_patterns') or [])}",
                f"- Match rationale: {l3.get('match_rationale', '')}",
                f"- Score breakdown: {l3.get('score_breakdown', {})}",
                f"- Fix summary: {l3.get('fix_summary', '')}",
            ]
        )
    lines.extend(
        [
            "",
            "LLM similarity analysis guidance:",
            "- Treat L1 as the strongest direct file-level prior evidence.",
            "- Treat L2 as same-repo semantic pattern evidence.",
            "- Treat L3 as cross-repo semantic pattern evidence.",
            "- Reuse only the parts that agree with the current repository, workflow, and failing evidence.",
        ]
    )
    return "\n".join(lines)
