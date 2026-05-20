# SWE-Memory Baseline for CI Repair: Complete Guide

This repository implements a **hierarchical memory retrieval system** for CI repair using SWE-agent. The system evaluates whether adding context from previously-solved CI failures improves a SWE-agent's ability to fix new CI failures.

**Key Research Question**: *Does adding CI-aware retrieval memory improve SWE-agent's performance on recurring CI failures?*

## 🎯 Quick Navigation

- **Getting Started**: See [docs/SETUP_GUIDE.md](docs/SETUP_GUIDE.md) for installation and local execution
- **Algorithm Details**: See [docs/ALGORITHM.md](docs/ALGORITHM.md) for high-level overview of 3-level memory hierarchy
- **CI/CD Integration**: See [docs/CI_INTEGRATION.md](docs/CI_INTEGRATION.md) for GitHub Actions, GitLab CI, and Docker setup
- **Environment Config**: Copy `.env.example` to `.env` and customize for your system

## 📋 What's Included

### Core System

```
sweagent/benchmark/
├── ci_memory_plugin.py         # Memory retrieval & ranking (L1, L2, L3)
├── ci_repair_memory.py         # Memory bank construction
├── ci_problem_document.py      # CI artifact analysis
└── path_config.py              # Dynamic path resolution
```

### Pipeline Scripts

```
scripts/
├── prepare_ci_repair_bench.py          # ① Split dataset into seed/eval
├── build_ci_problem_documents.py       # ② Analyze CI logs + workflows
├── build_ci_repair_memory_bank.py      # ③ Build L1, L2, L3 memory
├── build_ci_repair_instances.py        # ④ Create agent instances
└── run_full_benchmark.sh               # Automated end-to-end pipeline
```

### Configuration

```
config/benchmarks/
└── ci_repair_memory.yaml       # SWE-agent prompt template with memory injection

.env.example                     # Environment variables (paths, API keys, config)
```

### Documentation

```
docs/
├── ALGORITHM.md                 # High-level algorithm explanation
├── SETUP_GUIDE.md              # Local setup and execution
└── CI_INTEGRATION.md           # CI/CD pipeline integration
```

## 🚀 5-Minute Quick Start

```bash
# 1. Setup
git clone https://github.com/RabeyaMuna/swe-memory-baseline.git
cd swe-memory-baseline
python3.11 -m venv venv && source venv/bin/activate
pip install -e ".[dev]"

# 2. Configure (set your API key)
cp .env.example .env
# Edit .env - add OPENAI_API_KEY or MINIMAX_API_KEY

# 3. Run benchmark
bash scripts/run_full_benchmark.sh

# Results: artifacts/ci_repair_split/benchmark_results.json
```

**Expected Runtime**: 2-12 hours (depending on eval set size and API limits)

## 🔍 Understanding the System

### Memory Hierarchy (3 Levels)

The system uses a **hierarchical memory** with decreasing specificity:

#### **L1: File-Level Fixes** (Weight: 60%)
- **Specificity**: High (exact file + error type)
- **Content**: Specific repairs that fixed identical issues before
- **Example**: "When ruff reports F401 unused import in `src/models.py`, remove the import line"
- **Retrieval**: Cosine/Jaccard similarity on error + workflow + file context

#### **L2: Repository-Level Patterns** (Weight: 30%)
- **Specificity**: Medium (same repo + workflow + error type)
- **Content**: Common patterns for recurring issues in a repo
- **Example**: "In huggingface/diffusers CI tests, import errors typically require fixing `__init__.py`"
- **Retrieval**: Aggregate L1 matches by `(repo, workflow, error_type)`

#### **L3: Cross-Repository Patterns** (Weight: 10%)
- **Specificity**: Low (generalizable across repos)
- **Content**: General patterns that work across repositories
- **Example**: "Ruff F401 errors are usually fixed by removing imports (75% of cases)"
- **Retrieval**: Aggregate L1 matches by `(error_type, failed_tool)`

### End-to-End Pipeline

```
┌─────────────────────────────────────────────┐
│ CI Repair Benchmark (HuggingFace Dataset)   │
└──────────────────┬──────────────────────────┘
                   │
        ┌──────────┴──────────┐
        ↓                     ↓
    SEED 30%             EVAL 70%
    (for memory)         (for testing)
        │                     │
        ↓                     ↓
   Analyze CI Artifacts  Analyze CI Artifacts
   (extract context)     (extract context)
        │                     │
        └──────────┬──────────┘
                   ↓
         Build Memory Bank
         ├─ L1: File-level
         ├─ L2: Repo-level
         └─ L3: Cross-repo
                   │
        ┌──────────┴──────────┐
        ↓                     ↓
   BASELINE RUN         MEMORY RUN
   (no memory)          (with retrieval)
        │                     │
        ├─ Create instances   ├─ Retrieve matches
        ├─ Run SWE-agent      ├─ Inject memory
        └─ Measure success    └─ Run SWE-agent
                              └─ Measure success
                   │
                   ↓
            EVALUATE RESULTS
            Compare success rates
```

### Key Components

**1. Memory Building** (`build_ci_repair_memory_bank.py`)
- Takes seed rows (30% of dataset, stratified by repo)
- Extracts features: error types, failed commands, affected files, patch patterns
- Builds L1 records (one per affected file per failure)
- Aggregates L1 into L2 (repo-level) and L3 (cross-repo)
- Outputs: `memory_bank.json` with ~1000s L1, ~100s L2, ~50s L3 records

**2. Memory Retrieval** (`ci_memory_plugin.py`)
- Extracts context from new issue
- Scores against all L1, L2, L3 records using:
  - Cosine similarity: tokenized context overlap
  - Jaccard similarity: token set overlap
- Selects top matches above thresholds (L1: 0.30, L2: 0.40, L3: 0.50)
- Renders matches into plain text for prompt injection

**3. Prompt Injection** (`config/benchmarks/ci_repair_memory.yaml`)
- Memory context injected into agent's `instance_template`
- Agent sees: problem statement + retrieved memory context
- Agent is instructed to use memory as "non-binding hints only"
- Allows comparison: baseline (no memory) vs. augmented (with memory)

**4. Evaluation**
- Success metric: Did agent fix the CI failure?
- Comparison: `memory_success_rate - baseline_success_rate = improvement`
- Additional metrics: Retrieved match quality, ablation studies (L1 vs L1+L2 vs L1+L2+L3)

## 🔧 Configuration

All paths and settings are configurable via environment variables. See `.env.example` for complete list:

```bash
# Key variables
BENCHMARK_WORK_DIR=./artifacts                    # Output directory
CI_BENCHMARK_DATASET=ci-benchmark-user/ci-repair-bench
CI_MEMORY_SEED_RATIO=0.3                          # Seed split %
MEMORY_ABLATION_LEVELS=L1+L2+L3                   # Which levels to use
MEMORY_TOP_K=3                                    # Top-K L1 matches
CI_ARTIFACT_ANALYSIS_MODEL=gpt-4o-mini           # For analyzing logs
AGENT_MODEL=gpt-4o-mini                          # For repairs
DEPLOYMENT_TYPE=local                            # local or docker
CI_MODE=true                                     # Disable interactive
```

**Dynamic Path Support**:
- All scripts resolve paths relative to `SWE_AGENT_CONFIG_ROOT`
- Use `~` for home directory, `./` for relative, `/` for absolute
- Paths are environment-variable-aware: `${VAR_NAME}`

## 📊 Running the Benchmark

### Option 1: Automated (Recommended)

```bash
# One command runs everything
bash scripts/run_full_benchmark.sh
```

### Option 2: Step-by-Step

```bash
# 1. Prepare dataset
python scripts/prepare_ci_repair_bench.py \
  --output-dir artifacts/ci_repair_split

# 2. Analyze CI artifacts
python scripts/build_ci_problem_documents.py \
  --input-file artifacts/ci_repair_split/memory_seed_rows.jsonl \
  --output-file artifacts/ci_repair_split/memory_seed_analyzed.jsonl \
  --mode heuristic

python scripts/build_ci_problem_documents.py \
  --input-file artifacts/ci_repair_split/eval_rows.jsonl \
  --output-file artifacts/ci_repair_split/eval_analyzed.jsonl \
  --mode heuristic

# 3. Build memory
python scripts/build_ci_repair_memory_bank.py \
  --seed-file artifacts/ci_repair_split/memory_seed_analyzed.jsonl \
  --output-file artifacts/ci_repair_split/memory_bank.json

# 4. Create instances
python scripts/build_ci_repair_instances.py \
  --eval-file artifacts/ci_repair_split/eval_analyzed.jsonl \
  --output-file artifacts/ci_repair_split/instances_baseline.json \
  --memory-mode baseline

python scripts/build_ci_repair_instances.py \
  --eval-file artifacts/ci_repair_split/eval_analyzed.jsonl \
  --output-file artifacts/ci_repair_split/instances_memory.json \
  --memory-mode memory \
  --memory-bank artifacts/ci_repair_split/memory_bank.json

# 5. Run agents
sweagent run batch \
  --config-file config/benchmarks/ci_repair_memory.yaml \
  --instances-file artifacts/ci_repair_split/instances_baseline.json \
  --output-dir artifacts/ci_repair_split/runs/baseline

sweagent run batch \
  --config-file config/benchmarks/ci_repair_memory.yaml \
  --instances-file artifacts/ci_repair_split/instances_memory.json \
  --output-dir artifacts/ci_repair_split/runs/memory

# 6. Compare results
python scripts/evaluate_benchmark.py \
  --baseline-dir artifacts/ci_repair_split/runs/baseline \
  --memory-dir artifacts/ci_repair_split/runs/memory \
  --output-report artifacts/ci_repair_split/benchmark_results.json
```

## 🐳 Docker & CI/CD

### Quick Docker Run

```bash
docker build -t swe-memory-ci-repair:latest .
docker run --rm \
  -e MINIMAX_API_KEY=$MINIMAX_API_KEY \
  -v $(pwd)/artifacts:/workspace/artifacts \
  swe-memory-ci-repair:latest \
  bash scripts/run_full_benchmark.sh
```

### GitHub Actions Integration

See [docs/CI_INTEGRATION.md](docs/CI_INTEGRATION.md) for complete GitHub Actions workflow that:
- Runs on schedule or manual trigger
- Uploads results as artifacts
- Comments on PRs with results
- Compares baseline vs. memory metrics

### GitLab CI / Jenkins / Other

All scripts use environment variables and support any CI/CD platform. Key requirements:
- Python 3.11+
- API keys for LLM (OpenAI, MiniMax, etc.)
- Disk space for artifacts
- Network access to HuggingFace, GitHub

## 📈 Output Files

After running the benchmark, you'll have:

```
artifacts/ci_repair_split/
├── memory_seed_rows.jsonl              # Original seed data
├── memory_seed_analyzed.jsonl          # Seed + extracted CI context
├── eval_rows.jsonl                     # Original eval data
├── eval_analyzed.jsonl                 # Eval + extracted CI context
├── memory_bank.json                    # L1, L2, L3 hierarchical memory
├── instances_baseline.json             # Instances without memory
├── instances_memory.json               # Instances with memory context injected
├── retrieval_report.json               # Per-instance retrieval statistics
├── runs/
│   ├── baseline/                       # Agent execution logs (baseline)
│   │   ├── *.md                        # Trajectories
│   │   └── *.info.json                 # Summary (success/failure)
│   └── memory/                         # Agent execution logs (memory)
│       ├── *.md                        # Trajectories  
│       └── *.info.json                 # Summary (success/failure)
└── benchmark_results.json              # Final comparison (success rates, improvement)
```

## 🧪 Ablation Studies

Test different memory configurations:

```bash
# L1 only (file-level specificity)
python scripts/build_ci_repair_instances.py \
  --eval-file artifacts/ci_repair_split/eval_analyzed.jsonl \
  --output-file artifacts/ci_repair_split/instances_l1.json \
  --memory-mode memory \
  --memory-bank artifacts/ci_repair_split/memory_bank.json \
  --ablation-levels L1

# L1+L2 (add repo-level patterns)
python scripts/build_ci_repair_instances.py \
  --eval-file artifacts/ci_repair_split/eval_analyzed.jsonl \
  --output-file artifacts/ci_repair_split/instances_l1l2.json \
  --memory-mode memory \
  --memory-bank artifacts/ci_repair_split/memory_bank.json \
  --ablation-levels L1+L2

# Full: L1+L2+L3 (all levels, default)
# (already covered above)
```

## 🔬 Understanding Results

### Sample Output

```json
{
  "baseline_success": 45,
  "baseline_total": 150,
  "baseline_rate": 30.0,
  "memory_success": 62,
  "memory_total": 150,
  "memory_rate": 41.3,
  "improvement_rate": 11.3,
  "ablation": "L1+L2+L3"
}
```

**Interpretation**:
- Baseline: 30% of issues fixed without memory
- With Memory: 41.3% of issues fixed
- Improvement: +11.3 percentage points
- This means memory helped fix an additional 17 issues out of 150 (11.3%)

### Metrics to Track

- **Success Rate**: % of CI issues fixed
- **Improvement**: Absolute gain from memory
- **Retrieval Hit Rate**: % of issues where memory matches were found
- **Ablation Sensitivity**: How much each level (L1, L2, L3) contributes

## 🛠️ Troubleshooting

| Issue | Solution |
|-------|----------|
| `ImportError: cannot import name 'build_hierarchical_memory_bank'` | Run `pip install -e .` in repo |
| `API rate limit exceeded` | Use `CI_ANALYSIS_MODE=heuristic` or add delays |
| `HuggingFace auth error` | Run `huggingface-cli login` or set `HF_TOKEN` env var |
| `Out of memory` | Reduce eval set size or use smaller model |
| `Hardcoded path not found` | Set `BENCHMARK_WORK_DIR` and `SWE_AGENT_CONFIG_ROOT` env vars |

See [docs/SETUP_GUIDE.md](docs/SETUP_GUIDE.md) for more troubleshooting.

## 📚 Documentation

- **[ALGORITHM.md](docs/ALGORITHM.md)**: Deep dive into the 3-level memory hierarchy, retrieval algorithm, similarity metrics
- **[SETUP_GUIDE.md](docs/SETUP_GUIDE.md)**: Step-by-step local setup, configuration, and execution
- **[CI_INTEGRATION.md](docs/CI_INTEGRATION.md)**: GitHub Actions, Docker, and other CI/CD integration

## 🎓 How It Works (Very High-Level)

1. **Memory Construction**: Analyze 30% of benchmark data, extract features (error type, affected files, fix patterns), build hierarchical memory (L1 specific, L2 repo-level, L3 general)

2. **Memory Retrieval**: For each new CI issue, compute similarity against all memory records using semantic matching

3. **Prompt Injection**: Inject top-ranked memories into the agent's prompt, instructing it to use them as non-binding hints

4. **Agent Execution**: Run SWE-agent (with and without memory) on the remaining 70% of data

5. **Evaluation**: Compare success rates and measure memory's contribution

## 🤝 Contributing

To extend or improve this system:

1. **Add new retrieval strategies**: Modify `ci_memory_plugin.py`
2. **Improve memory construction**: Edit `ci_memory_plugin.py` (L1/L2/L3 building)
3. **Adjust similarity metrics**: Tune `_cosine_similarity`, `_jaccard_text_similarity`
4. **Test different ablations**: Use `--ablation-levels` flag in `build_ci_repair_instances.py`
5. **Integrate custom models**: Use any LLM via LiteLLM in `build_ci_problem_documents.py`

## 📄 Citation

If you use this system in your research, please cite:

```bibtex
@article{swe-memory-baseline,
  title={SWE-Memory: Hierarchical Memory for CI Repair},
  year={2024}
}
```

## 📜 License

See [LICENSE](LICENSE) for details.

## 🤔 FAQ

**Q: Does this modify SWE-agent's core loop?**  
A: No. Memory is purely injected as additional context in the prompt. The agent's action space and decision-making remain unchanged.

**Q: How long does the full pipeline take?**  
A: Typically 2-12 hours depending on eval set size and API rate limits. Memory building is fast (<1 min). Agent execution is the bottleneck (1-5 min per issue × N issues).

**Q: Can I run this in my CI/CD pipeline?**  
A: Yes! See [docs/CI_INTEGRATION.md](docs/CI_INTEGRATION.md) for GitHub Actions, GitLab CI, and Docker examples.

**Q: Can I customize the memory levels?**  
A: Yes! Use `--ablation-levels` to test L1 only, L1+L2, or L1+L2+L3. Adjust weights and thresholds in code or via environment variables.

**Q: What LLMs are supported?**  
A: Any LLM supported by LiteLLM: OpenAI, Anthropic, MiniMax, local models via Ollama, etc.

**Q: How does this compare to other memory/retrieval systems?**  
A: This uses a domain-specific (CI repair) 3-level hierarchy rather than generic vector retrieval. L1 is specific, L2 is repo-level, L3 is general. This reflects the structure of CI failures.

---

**For support**: Open an issue or check existing documentation.
