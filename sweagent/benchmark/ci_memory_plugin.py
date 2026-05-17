from __future__ import annotations

from collections import Counter, defaultdict
from math import sqrt
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
L2_WEIGHT = 0.25
L3_WEIGHT = 0.15


def _normalize_str_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value or "").strip()
    return [text] if text else []


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
    current_commands = _extract_failed_commands(logs)
    failed_tool = _infer_failed_tool(current_commands, logs)
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
    if current["repo"] != str(record.get("repo") or ""):
        return None
    file_overlap = _file_pattern_similarity(current, record)
    error_type_match = _error_type_similarity(current, record)
    text_similarity = _text_similarity(current, record)
    failed_job_match = _failed_job_similarity(current, record)
    failure_reason_similarity = _failure_reason_similarity(current, record)
    score_breakdown = {
        "file_overlap": 0.30 * file_overlap,
        "error_type_match": 0.20 * error_type_match,
        "text_similarity": 0.20 * text_similarity,
        "failed_job_match": 0.15 * failed_job_match,
        "failure_reason_similarity": 0.15 * failure_reason_similarity,
    }
    return _annotate_match(
        record,
        score=sum(score_breakdown.values()),
        score_breakdown=score_breakdown,
        rationale="L1 direct match uses same-repo file overlap, error type overlap, failed-job overlap, text similarity, and failure-reason similarity.",
    )


def _score_l2(current: dict[str, Any], record: dict[str, Any]) -> dict[str, Any] | None:
    if current["repo"] != str(record.get("repo") or ""):
        return None
    cosine_similarity = _cosine_similarity(current["retrieval_document"], str(record.get("retrieval_document") or ""))
    workflow_similarity = _workflow_similarity(current, record)
    error_type_match = _error_type_similarity(current, record)
    text_similarity = _text_similarity(current, record)
    file_pattern_similarity = _file_pattern_similarity(current, record)
    score_breakdown = {
        "cosine_similarity": 0.45 * cosine_similarity,
        "workflow_similarity": 0.20 * workflow_similarity,
        "error_type_match": 0.15 * error_type_match,
        "text_similarity": 0.10 * text_similarity,
        "file_pattern_similarity": 0.10 * file_pattern_similarity,
    }
    return _annotate_match(
        record,
        score=sum(score_breakdown.values()),
        score_breakdown=score_breakdown,
        rationale="L2 repo-level match uses cosine-heavy semantic similarity within the same repo, then reranks with workflow, error type, text, and file-pattern similarity.",
    )


def _score_l3(current: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    cosine_similarity = _cosine_similarity(current["retrieval_document"], str(record.get("retrieval_document") or ""))
    error_type_match = _error_type_similarity(current, record)
    tool_similarity = 1.0 if str(record.get("failed_tool") or "") == str(current.get("failed_tool") or "") else 0.0
    text_similarity = _text_similarity(current, record)
    score_breakdown = {
        "cosine_similarity": 0.55 * cosine_similarity,
        "error_type_match": 0.20 * error_type_match,
        "tool_similarity": 0.15 * tool_similarity,
        "text_similarity": 0.10 * text_similarity,
    }
    return _annotate_match(
        record,
        score=sum(score_breakdown.values()),
        score_breakdown=score_breakdown,
        rationale="L3 cross-repo match uses cosine-heavy semantic similarity across repos, plus error type, tool, and text similarity.",
    )


def retrieve_hierarchical_memory(
    row: dict[str, Any],
    memory_bank: dict[str, Any],
    *,
    top_k_l1: int = 3,
    min_l1: float = 0.15,
    min_l2: float = 0.20,
    min_l3: float = 0.20,
) -> dict[str, Any]:
    current = _current_issue_context(row)
    l1_candidates = []
    issue_id = f"{row.get('repo_owner', '')}__{row.get('repo_name', '')}-{row.get('id', '')}"
    for record in memory_bank.get("l1") or []:
        if str(record.get("issue_memory_id") or "") == issue_id:
            continue
        match = _score_l1(current, record)
        if match and float(match.get("similarity_score") or 0.0) >= min_l1:
            match["memory_level"] = "L1"
            l1_candidates.append(match)
    l1_candidates.sort(key=lambda item: float(item.get("similarity_score") or 0.0), reverse=True)
    l1_candidates = l1_candidates[:top_k_l1]

    l2_candidates = []
    for record in memory_bank.get("l2") or []:
        match = _score_l2(current, record)
        if match and float(match.get("similarity_score") or 0.0) >= min_l2:
            match["memory_level"] = "L2"
            l2_candidates.append(match)
    l2_candidates.sort(key=lambda item: float(item.get("similarity_score") or 0.0), reverse=True)
    l2_candidate = l2_candidates[0] if l2_candidates else None

    l3_candidates = []
    for record in memory_bank.get("l3") or []:
        match = _score_l3(current, record)
        if float(match.get("similarity_score") or 0.0) >= min_l3:
            match["memory_level"] = "L3"
            l3_candidates.append(match)
    l3_candidates.sort(key=lambda item: float(item.get("similarity_score") or 0.0), reverse=True)
    l3_candidate = l3_candidates[0] if l3_candidates else None

    level_scores = {
        "L1": max((float(item.get("similarity_score") or 0.0) for item in l1_candidates), default=0.0),
        "L2": float(l2_candidate.get("similarity_score") or 0.0) if l2_candidate else 0.0,
        "L3": float(l3_candidate.get("similarity_score") or 0.0) if l3_candidate else 0.0,
    }
    weighted_similarity = round(
        L1_WEIGHT * level_scores["L1"] + L2_WEIGHT * level_scores["L2"] + L3_WEIGHT * level_scores["L3"], 4
    )
    selected_levels = [level for level, score in level_scores.items() if score > 0.0]
    llm_similarity_candidates = l1_candidates[:3]
    if l2_candidate:
        llm_similarity_candidates.append(l2_candidate)
    if l3_candidate:
        llm_similarity_candidates.append(l3_candidate)
    return {
        "current_issue_context": current,
        "l1_candidates": l1_candidates,
        "l2_candidate": l2_candidate,
        "l3_candidate": l3_candidate,
        "level_scores": level_scores,
        "weighted_similarity": weighted_similarity,
        "selected_memory_levels": selected_levels,
        "llm_similarity_candidates": llm_similarity_candidates,
        "use_memory": weighted_similarity > 0.0,
    }


def render_hierarchical_memory_context(result: dict[str, Any]) -> str:
    if not result.get("use_memory"):
        return ""
    current = result.get("current_issue_context") or {}
    lines = [
        "Retrieved prior CI repair memory. Use it as non-binding prior experience only.",
        f"Weighted similarity: {float(result.get('weighted_similarity') or 0.0):.2f}",
        "Current issue summary:",
        f"- Repo: {current.get('repo', '')}",
        f"- Workflow: {current.get('workflow_name', '')} [{current.get('workflow_path', '')}]",
        f"- Error types: {', '.join(current.get('error_types') or [])}",
        f"- Failed jobs: {', '.join(current.get('failed_jobs') or [])}",
        f"- Affected files: {', '.join((current.get('affected_files') or current.get('changed_files') or [])[:6])}",
    ]
    for candidate in result.get("l1_candidates") or []:
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
    l2 = result.get("l2_candidate")
    if l2:
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
    l3 = result.get("l3_candidate")
    if l3:
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
