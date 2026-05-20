# System Architecture & Quick Visual Guide

## Complete System Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│         SWE-Memory Baseline: CI Repair Benchmark System             │
│                  (Dynamic Paths, Full CI/CD Support)               │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                      INPUT DATA LAYER                               │
├─────────────────────────────────────────────────────────────────────┤
│  HuggingFace Dataset: ci-benchmark-user/ci-repair-bench            │
│  ├─ Repo name, workflow, error logs, CI YAML, diffs, file changes  │
│  └─ Each row = one CI failure case                                 │
└──────────────────────┬──────────────────────────────────────────────┘
                       │
        ┌──────────────┴──────────────┐
        ↓                             ↓
    [MEMORY SEED]              [EVALUATION SET]
    30% of rows                70% of rows
    (stratified)               (stratified)
        │                             │
        │  ┌─ STEP 1: Prepare Data ─┤
        │  │  (prepare_ci_repair_bench.py)
        │  │  - Split by repo
        │  │  - Stratify by error type
        │  └─────────────────────────┤
        │                             │
        ↓                             ↓
    [ANALYZE SEED]          [ANALYZE EVAL]
    ↓                             ↓
    STEP 2a: Extract CI Context   STEP 2b: Extract CI Context
    (build_ci_problem_documents.py)
    ├─ Parse workflow YAML
    ├─ Extract error types
    ├─ Tokenize logs
    ├─ Identify affected files
    └─ Classify failure patterns
        │                             │
        ↓                             ↓
    [SEED w/ CONTEXT]       [EVAL w/ CONTEXT]
    (memory_seed_analyzed.jsonl) (eval_analyzed.jsonl)
        │                             │
        │                             │
        │  ┌─ STEP 3: Build Memory Bank ──────┐
        │  │  (build_ci_repair_memory_bank.py) │
        │  │                                    │
        │  ├─ L1: File-Level Records ────────┐│
        │  │  (per file per failure)          ││
        │  │  Weight: 60%                     ││
        │  │  Records: ~1000s                 ││
        │  │  [repo, workflow, file,          ││
        │  │   error_types, fix_patterns,     ││
        │  │   failed_tool, validation_cmd]   ││
        │  └─────────────────────────┬────────┘│
        │                             │         │
        │  ├─ L2: Repo-Level Patterns ───────┐│
        │  │  (aggregate L1 by repo+workflow) ││
        │  │  Weight: 30%                     ││
        │  │  Records: ~100s                  ││
        │  │  [repo, workflow, error_type,    ││
        │  │   common_files, common_patterns] ││
        │  └─────────────────────────┬────────┘│
        │                             │         │
        │  ├─ L3: Cross-Repo Patterns ──────┐ │
        │  │  (aggregate L1 by error+tool)    │ │
        │  │  Weight: 10%                     │ │
        │  │  Records: ~50s                   │ │
        │  │  [error_type, failed_tool,       │ │
        │  │   repos, common_patterns]        │ │
        │  └─────────────────────────┬────────┘ │
        │                             │         │
        └─────────────────────────────┼─────────┘
                                      ↓
                            [MEMORY BANK]
                      (memory_bank.json)
                      ├─ l1: ~1000 records
                      ├─ l2: ~100 records
                      └─ l3: ~50 records
                                      │
                ┌─────────────────────┴─────────────────────┐
                │                                           │
                │  ┌─ STEP 4: Create Instances ─────────┐  │
                │  │  (build_ci_repair_instances.py)     │  │
                │  │                                     │  │
                │  ├─ 4a: BASELINE INSTANCES ─────────┐ │  │
                │  │  (no memory)                      │ │  │
                │  │  ├─ For each eval row            │ │  │
                │  │  ├─ Add problem statement        │ │  │
                │  │  ├─ memory_context = ""          │ │  │
                │  │  └─ Output JSON with instance    │ │  │
                │  └─────────────────────────┬────────┘ │  │
                │                            │          │  │
                │  ├─ 4b: MEMORY INSTANCES ─────────┐  │  │
                │  │  (with retrieval)               │  │  │
                │  │  ├─ For each eval row           │  │  │
                │  │  ├─ Query memory bank           │  │  │
                │  │  ├─ Score L1, L2, L3 matches    │  │  │
                │  │  ├─ Select top-k by similarity  │  │  │
                │  │  ├─ Render memory context       │  │  │
                │  │  └─ Output JSON with instance   │  │  │
                │  └─────────────────────────┬────────┘  │  │
                │                            │           │  │
                └────────────────────────────┼───────────┘  │
                                             │              │
                ┌────────────────────────────┼──────────────┐
                ↓                            ↓              │
        [BASELINE INSTANCES]        [MEMORY INSTANCES]      │
        (instances_baseline.json)   (instances_memory.json) │
                │                            │              │
                │            [RETRIEVAL REPORT]             │
                │      (retrieval_report.json)              │
                │      per-instance retrieval stats         │
                │                                            │
                │  ┌─ STEP 5a: Run Agent (Baseline) ───┐   │
                │  │  (sweagent run batch)             │   │
                │  │  ├─ Config: ci_repair_memory.yaml │   │
                │  │  ├─ No memory context             │   │
                │  │  ├─ Agent attempts repair         │   │
                │  │  ├─ Success/failure recorded      │   │
                │  │  └─ Trajectories saved (.md)      │   │
                │  └──────────────┬─────────────────────┘   │
                │                 │                         │
                ↓                 ↓                         │
        [BASELINE RUNS]   [MEMORY RUNS]                     │
        (runs/baseline/)   (runs/memory/)                    │
        ├─ *.md            ├─ *.md                          │
        └─ *.info.json     └─ *.info.json                   │
                │                 │                         │
                │  ┌─ STEP 5b: Run Agent (Memory) ───┐    │
                │  │  (sweagent run batch)            │    │
                │  │  ├─ Config: ci_repair_memory.yaml│    │
                │  │  ├─ Inject memory context        │    │
                │  │  ├─ Agent attempts repair        │    │
                │  │  ├─ Success/failure recorded     │    │
                │  │  └─ Trajectories saved (.md)     │    │
                │  └──────────────┬────────────────────┘   │
                │                 │                        │
                │  ┌─ STEP 6: Evaluate Results ────────┐   │
                │  │  - Count successes (baseline)      │   │
                │  │  - Count successes (memory)        │   │
                │  │  - Compute improvement %           │   │
                │  │  - Save results.json               │   │
                │  └──────────────┬────────────────────┘   │
                │                 │                        │
                └─────────────────┴────────────────────────┘
                                  ↓
                        [FINAL RESULTS]
                    (benchmark_results.json)
                    ├─ baseline_success_rate: XX%
                    ├─ memory_success_rate: YY%
                    └─ improvement: +ZZ%


┌─────────────────────────────────────────────────────────────────────┐
│                  MEMORY RETRIEVAL DETAIL (STEP 4b)                  │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  For each new CI failure:                                          │
│  1. Extract context:                                              │
│     ├─ error_types, failed_tool, changed_files                   │
│     ├─ failed_commands, workflow_name, repo_id                   │
│     └─ Build retrieval_document (text representation)            │
│                                                                     │
│  2. Score against memory bank:                                    │
│     ├─ L1 Matching (File-Level):                                 │
│     │  ├─ For each L1 record in bank:                           │
│     │  │  ├─ Compute cosine similarity (token overlap)          │
│     │  │  ├─ Compute jaccard similarity (token union overlap)   │
│     │  │  ├─ Weighted score = 0.6×cosine + 0.4×jaccard         │
│     │  │  └─ If score >= 0.30: include as candidate            │
│     │  └─ Sort by score, take top-k                            │
│     │                                                             │
│     ├─ L2 Matching (Repo-Level):                                │
│     │  ├─ For each L2 record in bank:                          │
│     │  │  ├─ Score using same metrics                          │
│     │  │  └─ If score >= 0.40: include as candidate           │
│     │  └─ Take best match                                       │
│     │                                                             │
│     └─ L3 Matching (Cross-Repo):                                │
│        ├─ For each L3 record in bank:                          │
│        │  ├─ Score using same metrics                          │
│        │  └─ If score >= 0.50: include as candidate           │
│        └─ Take best match                                       │
│                                                                     │
│  3. Render memory context:                                        │
│     ├─ Format L1 matches: "File-level fix for X.py..."          │
│     ├─ Format L2 matches: "Repo-level pattern..."               │
│     ├─ Format L3 matches: "Cross-repo pattern..."               │
│     └─ Combine into memory_context string                       │
│                                                                     │
│  4. Inject into prompt:                                           │
│     ├─ Agent's prompt template includes:                        │
│     │  <problem_statement>...</problem_statement>              │
│     │  <retrieved_memory>{{memory_context}}</retrieved_memory> │
│     └─ Agent uses as "non-binding hints only"                   │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘


┌─────────────────────────────────────────────────────────────────────┐
│                   DYNAMIC PATH CONFIGURATION                         │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  Set in .env file or shell environment:                           │
│                                                                     │
│  BENCHMARK_WORK_DIR=./artifacts                                   │
│  └─ Base for all outputs (supports ~, ./, /, ${VAR})            │
│                                                                     │
│  BENCHMARK_OUTPUT_DIR=${BENCHMARK_WORK_DIR}/ci_repair_split      │
│  └─ Where all results are written                                │
│                                                                     │
│  SWE_AGENT_CONFIG_ROOT=.                                          │
│  └─ Base for relative path resolution                            │
│                                                                     │
│  Works on: localhost, any server, Docker, GitHub Actions, CI/CD   │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘


┌─────────────────────────────────────────────────────────────────────┐
│                    MEMORY LEVEL COMPARISON                          │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌────────────┬───────────┬──────────────────┬────────────────┐   │
│  │ Level      │ Weight    │ Specificity      │ Example         │   │
│  ├────────────┼───────────┼──────────────────┼────────────────┤   │
│  │ L1         │ 60%       │ HIGH             │ File-specific   │   │
│  │ (File)     │           │                  │ fix for X.py    │   │
│  │            │           │                  │                 │   │
│  │ L2         │ 30%       │ MEDIUM           │ Repo pattern    │   │
│  │ (Repo)     │           │                  │ for huggingface  │   │
│  │            │           │                  │ /diffusers      │   │
│  │            │           │                  │                 │   │
│  │ L3         │ 10%       │ LOW              │ General pattern │   │
│  │ (Cross)    │           │                  │ for any ruff    │   │
│  │            │           │                  │ F401 error      │   │
│  └────────────┴───────────┴──────────────────┴────────────────┘   │
│                                                                     │
│  Use cases:                                                         │
│  - L1 only: Maximum precision, narrow applicability               │
│  - L1+L2: Balanced, covers repo-specific patterns                 │
│  - L1+L2+L3 (default): Most comprehensive, generalizable          │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

## Key Files & Their Roles

```
📁 sweagent/benchmark/
├─ ci_memory_plugin.py          → Memory retrieval & ranking
│  ├─ build_hierarchical_memory_bank()  → L1, L2, L3 construction
│  ├─ retrieve_hierarchical_memory()    → Query & scoring
│  └─ render_hierarchical_memory_context() → Format for prompt
│
├─ ci_repair_memory.py          → Memory record building
│  ├─ build_memory_record()      → Extract from failure
│  ├─ split_rows_by_repo()       → Partition dataset
│  └─ normalize_logs()           → Clean CI logs
│
├─ ci_problem_document.py       → CI artifact analysis
│  ├─ heuristic_ci_problem_document()   → Rule-based analysis
│  └─ analyze_ci_artifacts_with_llm()  → LLM-based analysis
│
└─ path_config.py              → [NEW] Dynamic paths
   ├─ get_benchmark_work_dir()
   ├─ get_output_dir()
   ├─ resolve_path()
   └─ ensure_dir()

📁 scripts/
├─ prepare_ci_repair_bench.py
│  → STEP 1: Split dataset (30% seed, 70% eval)
│
├─ build_ci_problem_documents.py
│  → STEP 2: Analyze CI logs + workflows
│
├─ build_ci_repair_memory_bank.py
│  → STEP 3: Build memory bank (L1, L2, L3)
│
├─ build_ci_repair_instances.py
│  → STEP 4: Create SWE-agent instances
│
└─ run_full_benchmark.sh        → [NEW] Automated pipeline
   → STEPS 1-6: Run entire benchmark

📁 config/benchmarks/
└─ ci_repair_memory.yaml        → Agent config with memory injection

📁 docs/
├─ ALGORITHM.md                 → [NEW] Algorithm explained
├─ SETUP_GUIDE.md               → [NEW] Local setup
├─ CI_INTEGRATION.md            → [NEW] CI/CD setup
└─ README_COMPREHENSIVE.md      → [NEW] Complete guide

📄 .env.example                 → [UPDATED] All configurations
📄 IMPLEMENTATION_SUMMARY.md    → [NEW] What changed & how to use
```

## Environment Variable Cheat Sheet

```bash
# Copy and customize
cp .env.example .env

# Core paths (all support ~/./absolute and env vars)
BENCHMARK_WORK_DIR=./artifacts
BENCHMARK_OUTPUT_DIR=${BENCHMARK_WORK_DIR}/ci_repair_split
SWE_AGENT_CONFIG_ROOT=.

# Dataset & memory
CI_BENCHMARK_DATASET=ci-benchmark-user/ci-repair-bench
CI_MEMORY_SEED_RATIO=0.3
MEMORY_MODE=memory                    # baseline or memory
MEMORY_ABLATION_LEVELS=L1+L2+L3       # L1 / L1+L2 / L1+L2+L3

# API Keys (pick one)
OPENAI_API_KEY=sk-...                 # Option 1: OpenAI
# OR
MINIMAX_API_KEY=sk-or-v1-...          # Option 2: MiniMax/OpenRouter

# System
CI_MODE=true                          # CI/CD compatibility
LOG_LEVEL=INFO                        # DEBUG/INFO/WARNING/ERROR
```

## Common Commands

```bash
# Setup
python3.11 -m venv venv && source venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
# Edit .env with your API key

# Run full benchmark
bash scripts/run_full_benchmark.sh

# Run steps individually
python scripts/prepare_ci_repair_bench.py --output-dir ./artifacts/ci_repair_split
python scripts/build_ci_problem_documents.py --input-file ./artifacts/ci_repair_split/memory_seed_rows.jsonl --output-file ./artifacts/ci_repair_split/memory_seed_analyzed.jsonl --mode heuristic
python scripts/build_ci_repair_memory_bank.py --seed-file ./artifacts/ci_repair_split/memory_seed_analyzed.jsonl --output-file ./artifacts/ci_repair_split/memory_bank.json
python scripts/build_ci_repair_instances.py --eval-file ./artifacts/ci_repair_split/eval_analyzed.jsonl --output-file ./artifacts/ci_repair_split/instances_memory.json --memory-mode memory --memory-bank ./artifacts/ci_repair_split/memory_bank.json
sweagent run batch --config-file config/benchmarks/ci_repair_memory.yaml --instances-file ./artifacts/ci_repair_split/instances_memory.json --output-dir ./artifacts/ci_repair_split/runs/memory

# Run in Docker
docker build -t swe-memory-ci-repair:latest .
docker run --rm -e MINIMAX_API_KEY=$MINIMAX_API_KEY -v $(pwd)/artifacts:/workspace/artifacts swe-memory-ci-repair:latest bash scripts/run_full_benchmark.sh

# Customize paths on different machine
export BENCHMARK_WORK_DIR=/mnt/large_disk/results
bash scripts/run_full_benchmark.sh
```

---

**For detailed information**, see:
- [docs/ALGORITHM.md](../docs/ALGORITHM.md) - Detailed algorithm
- [docs/SETUP_GUIDE.md](../docs/SETUP_GUIDE.md) - Step-by-step setup
- [docs/CI_INTEGRATION.md](../docs/CI_INTEGRATION.md) - CI/CD integration
- [IMPLEMENTATION_SUMMARY.md](../IMPLEMENTATION_SUMMARY.md) - What changed
