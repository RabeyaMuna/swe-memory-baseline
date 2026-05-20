# High-Level Algorithm: SWE-Memory Hierarchical Retrieval System

## Overview

The SWE-Memory system adds a retrieval layer on top of SWE-agent for CI repair. Instead of modifying the agent's core loop, memory is injected as additional context in the prompt. The memory system uses a **three-level hierarchical approach** to retrieve relevant prior solutions for new CI failures.

```
┌─────────────────────────────────────────────────────┐
│           Failing CI Workflow Issue                 │
│  (repo, workflow, error_type, logs, changed_files)  │
└─────────────────────┬───────────────────────────────┘
                      │
                      ↓
        ┌─────────────────────────────┐
        │ Extract Issue Context (L0)  │
        │ - failed_tool               │
        │ - error_types               │
        │ - changed_files             │
        │ - failure_patterns          │
        └──────────────┬──────────────┘
                       │
        ┌──────────────┴──────────────┐
        │  Hierarchical Retrieval     │
        │                              │
        ├─→ L1 (File-level)      60%   │
        ├─→ L2 (Repo-level)      30%   │
        └─→ L3 (Cross-repo)      10%   │
                       │
                       ↓
        ┌─────────────────────────────┐
        │  Weighted Ranking           │
        │  (Similarity Scoring)       │
        └──────────────┬──────────────┘
                       │
                       ↓
        ┌─────────────────────────────┐
        │  Memory Context             │
        │  (Top matches + hints)      │
        └──────────────┬──────────────┘
                       │
                       ↓
        ┌─────────────────────────────┐
        │  SWE-Agent Prompt           │
        │  + memory_context injection │
        │  (minimal non-test changes) │
        └─────────────────────────────┘
```

## Three-Level Memory Hierarchy

### **Level 1 (L1): File-Level Specific Fixes** (Weight: 60%)

**Granularity**: File-specific repairs for exact CI failures

**Content per record**:
- `repo`: Repository identifier
- `workflow_name`, `workflow_path`: Which CI workflow
- `file`: Specific file that was fixed
- `error_type`: Type of failure (e.g., "Code Formatting")
- `failure_pattern`: What was wrong (e.g., "lint_or_format_failure")
- `failed_tool`: Tool that failed (e.g., "ruff", "pytest")
- `failed_commands`: Exact commands that ran
- `fix_pattern`: How it was fixed (e.g., "import_edit", "formatting")
- `validation_cmd`: Command to validate the fix
- `dependent_files`: Other files changed together
- `mentioned_tokens`: Keywords from error/fix

**Similarity Metrics**:
- Cosine similarity on tokenized error + workflow + file context
- Jaccard similarity on token sets
- File path overlap scoring

**Retrieval Threshold**: 0.30 (tunable)

**Example Match**:
```
Query: "ruff import F401 error in src/models.py"
Match: Past fix for same repo/workflow/file where import was added
Result: "When F401 unused import detected, add: from x import y"
```

### **Level 2 (L2): Repository-Level Patterns** (Weight: 30%)

**Granularity**: Recurring patterns within same repo + workflow + error_type

**Content per record**:
- `repo`: Repository 
- `workflow_name`: CI workflow name
- `error_type`: Class of error (aggregated from L1)
- `files`: Common files that are edited (all files touched by this pattern)
- `failed_commands`: Common command sequences
- `failure_patterns`: Aggregated failure types (from L1)
- `fix_patterns`: Aggregated fix styles (from L1)
- `reasons`: Common failure reasons

**Construction**: Aggregate L1 records by `(repo, workflow_name, error_type)` tuples

**Retrieval Logic**: 
- Match repo exactly
- Match error_type with high similarity
- Return common patterns for this context

**Retrieval Threshold**: 0.40 (tunable)

**Example Match**:
```
Query: "huggingface/diffusers repo, tests workflow, ImportError"
Match: L2 aggregation showing 5+ past ImportError fixes in tests
Result: "Typical files: src/__init__.py, tests/conftest.py
         Typical commands: pytest, python -m pytest
         Common fix: Add missing imports or remove broken ones"
```

### **Level 3 (L3): Cross-Repository Patterns** (Weight: 10%)

**Granularity**: Generalizable patterns across all repositories

**Content per record**:
- `error_type`: Error classification
- `failed_tool`: Tool that failed (e.g., "ruff", "mypy")
- `repos`: All repositories with this pattern
- `failure_patterns`: Aggregated patterns
- `fix_patterns`: Aggregated fix styles
- `mentioned_tokens`: Common keywords
- `reasons`: Common failure reasons

**Construction**: Aggregate L1 records by `(error_type, failed_tool)` tuples

**Retrieval Logic**:
- Match error_type with high similarity  
- Match failed_tool exactly
- Cross-repository insights (generalizable)

**Retrieval Threshold**: 0.50 (tunable, highest threshold)

**Example Match**:
```
Query: "Any repo, any workflow, F401 unused import error, ruff tool"
Match: L3 aggregation from 50+ repos with ruff F401 errors
Result: "Ruff F401 typically requires:
         - Remove unused import OR
         - Use noqa comment OR  
         - Add to __all__
         Successful pattern: 85% of fixes are import removals"
```

## Retrieval Algorithm

### Step 1: Extract Query Context from New Issue

```python
def extract_issue_context(row):
    """Convert new CI failure into searchable context"""
    query_context = {
        "repo": extract_repo_id(row),
        "workflow": row["workflow_name"],
        "error_types": normalize_error_types(row),
        "failed_tool": infer_failed_tool(row["logs"]),
        "failure_patterns": classify_failure_patterns(row),
        "changed_files": extract_changed_files(row),
        "failed_commands": extract_failed_commands(row),
        "retrieval_document": build_retrieval_text(row),
    }
    return query_context
```

### Step 2: Hierarchical Retrieval (L1 → L2 → L3)

```python
def retrieve_hierarchical_memory(query_row, memory_bank, top_k=3):
    """Retrieve best matches across L1, L2, L3"""
    
    # L1 Retrieval: File-level matches (most specific)
    l1_candidates = retrieve_l1_matches(
        query_row, 
        memory_bank["l1"],
        ablation="L1+L2+L3"
    )
    l1_matches = [m for m in l1_candidates if m["similarity"] >= 0.30]
    
    # L2 Retrieval: Repo-level patterns (medium specificity)
    l2_candidate = retrieve_l2_match(
        query_row,
        memory_bank["l2"],
        ablation="L1+L2+L3"
    )
    l2_matches = [l2_candidate] if l2_candidate and l2_candidate["similarity"] >= 0.40 else []
    
    # L3 Retrieval: Cross-repo patterns (least specific, most general)
    l3_candidate = retrieve_l3_match(
        query_row,
        memory_bank["l3"],
        ablation="L1+L2+L3"
    )
    l3_matches = [l3_candidate] if l3_candidate and l3_candidate["similarity"] >= 0.50 else []
    
    return {
        "l1_matches": l1_matches[:top_k],
        "l2_matches": l2_matches,
        "l3_matches": l3_matches,
        "use_memory": bool(l1_matches or l2_matches or l3_matches),
    }
```

### Step 3: Similarity Scoring

The system uses **component-based similarity scoring** with different emphasis per level.

#### Components

| Component | L1 Weight | L2 Weight | L3 Weight | Description |
|-----------|-----------|-----------|-----------|-------------|
| **file_match** | 0.35 | 0.00 | 0.00 | Exact file path match (L1 only) |
| **error_match** | 0.20 | 0.25 | 0.20 | Error type Jaccard overlap |
| **pattern_similarity** | 0.15 | 0.15 | 0.15 | Failure pattern cosine similarity |
| **tool_overlap** | 0.10 | 0.15 | 0.10 | Failed tool Jaccard overlap |
| **reason_similarity** | 0.20 | 0.45 | 0.55 | Failure reason TF-IDF cosine |

#### Scoring Strategy by Level

**L1 (File-Level)**: Emphasizes exact matches
$$\text{Score}_{L1} = 0.35 \times \text{file\_match} + 0.20 \times \text{error\_match} + 0.15 \times \text{pattern} + 0.10 \times \text{tool} + 0.20 \times \text{reason}$$

**Why**: File-level retrieval must be precise. Exact file + exact error type are most important.

**L2 (Repo-Level)**: Emphasizes error type and failure reason
$$\text{Score}_{L2} = 0.25 \times \text{error\_match} + 0.15 \times \text{pattern} + 0.15 \times \text{tool} + 0.45 \times \text{reason}$$

**Why**: No file matching at repo level. Focus on error classification and failure context (reason).

**L3 (Cross-Repo)**: Emphasizes failure reason (most general)
$$\text{Score}_{L3} = 0.20 \times \text{error\_match} + 0.15 \times \text{pattern} + 0.10 \times \text{tool} + 0.55 \times \text{reason}$$

**Why**: Cross-repo patterns must rely on very general evidence. Failure reason text (0.55 weight) dominates to ensure high-quality matches.

#### Similarity Metrics

**Cosine Similarity** (for patterns, tool overlap):
$$\text{cos\_sim}(A, B) = \frac{\sum_{t} A_t \cdot B_t}{\sqrt{\sum_t A_t^2} \cdot \sqrt{\sum_t B_t^2}}$$

**Jaccard Similarity** (for error types, tool overlap):
$$\text{jaccard}(A, B) = \frac{|A \cap B|}{|A \cup B|}$$

**TF-IDF Cosine Similarity** (for failure reason text):
- Emphasizes rare/distinguishing tokens
- Tokens appearing in only one document get higher weight (IDF)
- Prevents common stop-words from dominating the match
$$\text{TF-IDF\_cos}(A, B) = \frac{\sum_t \text{IDF}(t) \cdot A_t \cdot B_t}{\sqrt{\sum_t (\text{IDF}(t) \cdot A_t)^2} \cdot \sqrt{\sum_t (\text{IDF}(t) \cdot B_t)^2}}$$

#### Thresholds (Updated)

| Level | Threshold | Purpose |
|-------|-----------|---------|
| L1 | 0.40 | Exact file match failure patterns or reason of same repo |
| L2 | 0.40 | Moderate gate; repo failure patterns require broader match |
| L3 | 0.50 | Strict gate; cross-repo evidence must be high quality |

**Changed**: L1 threshold increased from 0.30 to 0.40 to ensure higher precision at file level.

#### Why Component-Based Scoring?

1. **Transparency**: Each component contribution is explicit and tunable
2. **Hierarchy Respect**: Specific evidence (file match) weights heavily at L1, generic evidence (reason text) weights heavily at L3
3. **Flexibility**: Can adjust weights per level without changing retrieval logic
4. **Quality Control**: Prevents low-quality matches by requiring high reason similarity at L3

### Step 4: Weighted Aggregation

```python
def compute_weighted_similarity(
    l1_scores, l2_scores, l3_scores, ablation="L1+L2+L3"
):
    """Compute final similarity considering all levels"""
    weights = {
        "L1": 0.60,  # File-level most important
        "L2": 0.30,  # Repo patterns medium importance
        "L3": 0.10,  # General patterns least important
    }
    
    # Normalize by ablation (e.g., if only L1 enabled, weight=1.0)
    active_levels = parse_ablation(ablation)
    active_sum = sum(weights[l] for l in active_levels)
    normalized_weights = {
        l: weights[l] / active_sum if l in active_levels else 0.0
        for l in ["L1", "L2", "L3"]
    }
    
    # Compute weighted score
    final_score = (
        normalized_weights["L1"] * max(l1_scores, default=0) +
        normalized_weights["L2"] * (l2_scores or 0) +
        normalized_weights["L3"] * (l3_scores or 0)
    )
    
    return final_score
```

## Memory Construction Pipeline

### Input: Seed Rows (30% of benchmark, stratified by repo)

```
Dataset: ci-repair-bench
├─ All rows
├─ Split by repo
└─ Take 30% per repo for seed (memory construction)
```

### Step 1: Build L1 Records (File-Level)

For each seed row:
1. Extract memory record from CI failure
2. Identify all affected files
3. Create one L1 record **per file per row**
4. Store file-specific context (error, pattern, fix, commands)

**Output**: L1 bank with ~1000s of file-level records

### Step 2: Build L2 Records (Repo-Level)

Group L1 records by `(repo, workflow_name, error_type)`:
1. Aggregate failed commands across group
2. Collect all files touched
3. Collect all failure/fix patterns
4. Compute common signatures

**Output**: L2 bank with ~100s of repo-level records

### Step 3: Build L3 Records (Cross-Repo)

Group L1 records by `(error_type, failed_tool)`:
1. Collect repositories
2. Aggregate patterns across all repos
3. Compute generalizable insights

**Output**: L3 bank with ~50s of general-pattern records

## Memory Context Rendering

When retrieved matches are found, they're rendered into plain text for prompt injection:

```
<retrieved_memory>
=== File-Level Fix (Similar to your issue) ===
Repository: huggingface/diffusers
File: src/models.py
Error: F401 unused import in ruff check
Pattern: import_edit (removed unused import)
Fix Summary: When ruff reports F401 on line X, remove the import statement
Validation: ruff check src/models.py

=== Repo-Level Pattern ===
Repository: huggingface/diffusers
Workflow: tests
Common files touched: __init__.py, conftest.py
Typical commands: pytest, python -m pytest
Common pattern: ImportError from missing dependencies

=== Cross-Repo Pattern ===
Error Type: import_or_symbol_issue  
Tool: ruff
Repos affected: 50+ (huggingface, pytorch, sklearn, ...)
Fix Patterns: [import_edit(75%), noqa_comment(20%), add_to_all(5%)]
</retrieved_memory>
```

## Ablation Modes

The system supports ablation (disabling certain memory levels):

- **L1**: Only file-level specific fixes (most specific)
- **L1+L2**: File + repo patterns (balanced)
- **L1+L2+L3**: Full hierarchy (default, most comprehensive)

Each ablation adjusts:
1. Weights (renormalized)
2. Thresholds (different global threshold per ablation)
3. Candidate selection (only enabled levels)

## Integration with SWE-Agent

### Template Injection Point

Memory context is injected into the agent's prompt template at the `instance_template`:

```yaml
agent:
  templates:
    instance_template: |
      <task>
      {{problem_statement}}
      </task>
      
      <retrieved_memory>
      {{memory_context}}        # ← Memory injected here
      </retrieved_memory>
      
      Use `retrieved_memory` only as non-binding prior experience.
      If it conflicts with repository evidence, ignore it.
```

### Agent Behavior

1. Agent receives problem statement + memory context
2. Agent is instructed to:
   - Use memory as **hints only** (non-binding)
   - Prioritize actual repository evidence
   - Ignore conflicting information
   - Make minimal non-test code changes
3. Memory doesn't change agent's action space or loop

## Key Metrics & Thresholds

| Metric | L1 | L2 | L3 | Global (L1+L2+L3) |
|--------|----|----|----|--------------------|
| Similarity Threshold | 0.30 | 0.40 | 0.50 | 0.33 |
| Weight | 0.60 | 0.30 | 0.10 | - |
| Specificity | High | Medium | Low | - |
| Candidate Count | ~1000s | ~100s | ~50s | - |

## Performance Characteristics

- **Memory Build Time**: O(n log n) for n seed rows (sorting + grouping)
- **Per-Query Retrieval**: O(k) where k = total memory records (~1500)
- **Similarity Computation**: Cosine + Jaccard both O(tokens^2), typically ~50-100 tokens
- **Total per Issue**: <100ms typical, <1s worst case

## Quality & Recall

The system is evaluated on:
1. **Memory Precision**: Relevance of retrieved matches to actual issue
2. **Agent Success Rate**: % of issues fixed with memory vs. without
3. **Latency**: Time to retrieve + render memory context
4. **Overhead**: Memory build & storage size
