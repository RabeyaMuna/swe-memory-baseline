# Quick Reference: SWE-Memory CI Repair System

## 🚀 5-Minute Start

```bash
# Setup
git clone https://github.com/RabeyaMuna/swe-memory-baseline.git
cd swe-memory-baseline
python3.11 -m venv venv && source venv/bin/activate
pip install -e ".[dev]"

# Configure
cp .env.example .env
# Edit .env - add OPENAI_API_KEY=sk-... or MINIMAX_API_KEY=sk-or-v1-...

# Run
bash scripts/run_full_benchmark.sh
```

**Results**: `artifacts/ci_repair_split/benchmark_results.json`

---

## 📊 Key Metrics

| Metric | L1 | L2 | L3 | Global (L1+L2+L3) |
|--------|----|----|----|--------------------|
| **Threshold** | **0.40** | 0.40 | 0.50 | 0.33 |
| **Weight** | 0.60 | 0.30 | 0.10 | - |
| **Specificity** | High | Medium | Low | - |
| **Candidate Count** | ~1000s | ~100s | ~50s | - |

**Scoring Components** (per level):
- **L1**: file_match (0.35) + error_match (0.20) + pattern (0.15) + tool (0.10) + reason_TF-IDF (0.20)
- **L2**: error_match (0.25) + pattern (0.15) + tool (0.15) + reason_TF-IDF (0.45)
- **L3**: error_match (0.20) + pattern (0.15) + tool (0.10) + reason_TF-IDF (0.55)

**Pipeline**:
1. Prepare dataset (30% seed, 70% eval)
2. Analyze CI artifacts (extract context)
3. Build memory bank (L1, L2, L3)
4. Create instances (baseline + memory)
5. Run SWE-agent (both modes)
6. Evaluate results (compare success rates)

---

## 📁 Key Files

| File | Purpose |
|------|---------|
| `scripts/run_full_benchmark.sh` | Automated pipeline (all steps) |
| `.env.example` | Configuration template |
| `docs/ALGORITHM.md` | Algorithm explanation |
| `docs/SETUP_GUIDE.md` | Local setup instructions |
| `docs/CI_INTEGRATION.md` | CI/CD integration |
| `docs/ARCHITECTURE.md` | System architecture |
| `sweagent/benchmark/path_config.py` | Dynamic path utilities |

---

## 🔧 Configuration (`.env`)

```bash
# Paths (supports ~, ./, /, env vars)
BENCHMARK_WORK_DIR=./artifacts
BENCHMARK_OUTPUT_DIR=${BENCHMARK_WORK_DIR}/ci_repair_split
SWE_AGENT_CONFIG_ROOT=.

# Dataset
CI_BENCHMARK_DATASET=ci-benchmark-user/ci-repair-bench
CI_MEMORY_SEED_RATIO=0.3

# Memory
MEMORY_MODE=memory  # or: baseline
MEMORY_ABLATION_LEVELS=L1+L2+L3  # or: L1, L1+L2
MEMORY_TOP_K=3

# Models
AGENT_MODEL=gpt-4o-mini
CI_ARTIFACT_ANALYSIS_MODEL=gpt-4o-mini

# API (pick one)
OPENAI_API_KEY=sk-...  # Option 1
# OR
MINIMAX_API_KEY=sk-or-v1-...  # Option 2

# System
CI_MODE=true
LOG_LEVEL=INFO
```

---

## 🎯 Commands

### Step-by-Step

```bash
# 1. Prepare data
python scripts/prepare_ci_repair_bench.py --output-dir artifacts/ci_repair_split

# 2. Analyze (heuristic = fast, no API)
python scripts/build_ci_problem_documents.py \
  --input-file artifacts/ci_repair_split/memory_seed_rows.jsonl \
  --output-file artifacts/ci_repair_split/memory_seed_analyzed.jsonl --mode heuristic

python scripts/build_ci_problem_documents.py \
  --input-file artifacts/ci_repair_split/eval_rows.jsonl \
  --output-file artifacts/ci_repair_split/eval_analyzed.jsonl --mode heuristic

# 3. Build memory
python scripts/build_ci_repair_memory_bank.py \
  --seed-file artifacts/ci_repair_split/memory_seed_analyzed.jsonl \
  --output-file artifacts/ci_repair_split/memory_bank.json

# 4. Create instances (baseline)
python scripts/build_ci_repair_instances.py \
  --eval-file artifacts/ci_repair_split/eval_analyzed.jsonl \
  --output-file artifacts/ci_repair_split/instances_baseline.json \
  --memory-mode baseline

# 4. Create instances (memory)
python scripts/build_ci_repair_instances.py \
  --eval-file artifacts/ci_repair_split/eval_analyzed.jsonl \
  --output-file artifacts/ci_repair_split/instances_memory.json \
  --memory-mode memory \
  --memory-bank artifacts/ci_repair_split/memory_bank.json

# 5. Run SWE-agent
sweagent run batch \
  --config-file config/benchmarks/ci_repair_memory.yaml \
  --instances-file artifacts/ci_repair_split/instances_baseline.json \
  --output-dir artifacts/ci_repair_split/runs/baseline

sweagent run batch \
  --config-file config/benchmarks/ci_repair_memory.yaml \
  --instances-file artifacts/ci_repair_split/instances_memory.json \
  --output-dir artifacts/ci_repair_split/runs/memory
```

### Automated (Recommended)

```bash
# All steps in one command
bash scripts/run_full_benchmark.sh
```

### Docker

```bash
docker build -t swe-memory:latest .
docker run --rm \
  -e MINIMAX_API_KEY=$MINIMAX_API_KEY \
  -v $(pwd)/artifacts:/workspace/artifacts \
  swe-memory:latest \
  bash scripts/run_full_benchmark.sh
```

### Custom Paths

```bash
export BENCHMARK_WORK_DIR=/mnt/large_disk/results
bash scripts/run_full_benchmark.sh
```

---

## 📈 Output Files

```
artifacts/ci_repair_split/
├── memory_seed_rows.jsonl           # Original seed data
├── memory_seed_analyzed.jsonl       # Seed with extracted context
├── eval_rows.jsonl                  # Original eval data
├── eval_analyzed.jsonl              # Eval with extracted context
├── memory_bank.json                 # L1, L2, L3 memory (~1000s + 100s + 50s records)
├── instances_baseline.json          # Agent instances (no memory)
├── instances_memory.json            # Agent instances (with memory)
├── retrieval_report.json            # Memory retrieval stats
├── runs/
│   ├── baseline/                    # Agent execution logs (baseline)
│   │   ├── *.md                     # Trajectories
│   │   └── *.info.json              # Success/failure
│   └── memory/                      # Agent execution logs (with memory)
│       ├── *.md                     # Trajectories
│       └── *.info.json              # Success/failure
└── benchmark_results.json           # Final comparison
```

---

## 📊 Results Interpretation

```json
{
  "baseline_success": 45,           # Issues fixed without memory
  "baseline_total": 150,            # Total baseline issues
  "baseline_rate": 30.0,            # 30% success without memory
  "memory_success": 62,             # Issues fixed with memory
  "memory_total": 150,              # Total memory issues
  "memory_rate": 41.3,              # 41.3% success with memory
  "improvement_rate": 11.3          # +11.3% improvement
}
```

**Interpretation**: Memory helped fix an additional 11.3% of issues.

---

## 🧪 Ablation Tests

```bash
# L1 only (most specific)
python scripts/build_ci_repair_instances.py \
  --memory-mode memory --ablation-levels L1 \
  ...

# L1+L2 (balanced)
python scripts/build_ci_repair_instances.py \
  --memory-mode memory --ablation-levels L1+L2 \
  ...

# L1+L2+L3 (full, default)
python scripts/build_ci_repair_instances.py \
  --memory-mode memory --ablation-levels L1+L2+L3 \
  ...
```

---

## ❓ Troubleshooting

| Problem | Solution |
|---------|----------|
| `ImportError` | `pip install -e .` |
| API rate limit | Use `CI_ANALYSIS_MODE=heuristic` |
| Out of memory | Reduce eval set size |
| HF dataset error | `huggingface-cli login` or set `HF_TOKEN` |
| Path not found | Set `BENCHMARK_WORK_DIR` env var |

---

## 📚 Documentation

| Document | Content |
|----------|---------|
| `docs/ALGORITHM.md` | Deep dive: algorithm, retrieval, metrics |
| `docs/SETUP_GUIDE.md` | Local setup, step-by-step, customization |
| `docs/CI_INTEGRATION.md` | GitHub Actions, Docker, CI/CD |
| `docs/ARCHITECTURE.md` | System architecture, visual diagrams |
| `docs/README_COMPREHENSIVE.md` | Complete overview |
| `IMPLEMENTATION_SUMMARY.md` | What changed, files modified |

---

## 🔍 Memory Retrieval Algorithm (Simplified)

For each new CI issue:

1. **Extract Context**: error_type, failed_tool, changed_files, logs, workflow
2. **Score Against Memory**:
   - L1: Compare against ~1000s file-level records
   - L2: Compare against ~100s repo-level patterns
   - L3: Compare against ~50s cross-repo patterns
3. **Similarity**: Cosine + Jaccard on tokenized text
4. **Filter**: Keep matches above threshold (L1: 0.30, L2: 0.40, L3: 0.50)
5. **Select**: Top-K (default: 3) L1 matches
6. **Render**: Format as plain text for prompt injection
7. **Execute**: Agent sees retrieved memory as "non-binding hints"

---

## 🎓 Memory Levels Explained

### L1: File-Level (60% weight)

**Specificity**: HIGH  
**Record Count**: ~1000s  
**Content**: `{repo, workflow, file, error_type, fix_pattern, validation_cmd, ...}`  
**Example**: "When ruff reports F401 unused import in `src/models.py`, remove the import"  

### L2: Repository-Level (30% weight)

**Specificity**: MEDIUM  
**Record Count**: ~100s  
**Content**: `{repo, workflow, error_type, common_files, common_patterns, ...}`  
**Example**: "In huggingface/diffusers, import errors typically need fixing `__init__.py`"  

### L3: Cross-Repository (10% weight)

**Specificity**: LOW  
**Record Count**: ~50s  
**Content**: `{error_type, failed_tool, repos, patterns, ...}`  
**Example**: "Ruff F401 errors are usually fixed by removing imports (75% success)"  

---

## 🌐 Supported Environments

✅ Linux, macOS, Windows (with WSL)  
✅ Local machine  
✅ Docker container  
✅ GitHub Actions  
✅ GitLab CI  
✅ Jenkins  
✅ Any CI/CD system (bash + Python 3.11+)  

---

## 💡 Tips & Tricks

```bash
# Test with small eval set
python scripts/prepare_ci_repair_bench.py --memory-ratio 0.5 --output-dir test_artifacts

# Speed up with heuristic analysis (no API calls)
export CI_ANALYSIS_MODE=heuristic

# View memory bank stats
python -c "
import json
with open('artifacts/ci_repair_split/memory_bank.json') as f:
    b = json.load(f)
    print(f'L1: {len(b[\"l1\"])}, L2: {len(b[\"l2\"])}, L3: {len(b[\"l3\"])}')
"

# Check retrieval effectiveness
python -c "
import json
with open('artifacts/ci_repair_split/retrieval_report.json') as f:
    r = json.load(f)
    used = sum(1 for x in r if x.get('memory_used'))
    print(f'Memory used in {used}/{len(r)} instances')
"

# Keep temp files for inspection
export KEEP_TEMP_FILES=true
```

---

**For more details, see the documentation in `docs/`**
