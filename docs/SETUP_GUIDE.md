# Setup & Quick Start Guide: Running the SWE-Memory Benchmark Locally

## 5-Minute Quick Start

```bash
# 1. Clone and setup
git clone https://github.com/RabeyaMuna/swe-memory-baseline.git
cd swe-memory-baseline
python3.11 -m venv venv && source venv/bin/activate
pip install -e ".[dev]"

# 2. Configure API key
cp .env.example .env
# Edit .env and add your MINIMAX_API_KEY or OPENAI_API_KEY

# 3. Run full benchmark
bash scripts/run_full_benchmark.sh

# Results in: artifacts/ci_repair_split/benchmark_results.json
```

## Full Setup Instructions

### 1. Prerequisites

- **Python**: 3.11 or later
- **Git**: For repository management
- **API Key**: OpenAI, MiniMax, or compatible LLM provider
- **Disk Space**: ~5-10GB for dataset and runs
- **Network**: GitHub, HuggingFace Datasets access

### 2. Environment Setup

#### Option A: Using Virtual Environment (Recommended)

```bash
# Create virtual environment
python3.11 -m venv venv

# Activate
# Linux/Mac:
source venv/bin/activate
# Windows:
venv\Scripts\activate

# Upgrade pip
pip install --upgrade pip setuptools wheel

# Install project
pip install -e ".[dev]"
```

#### Option B: Using Conda

```bash
conda create -n swe-memory python=3.11
conda activate swe-memory
pip install -e ".[dev]"
```

### 3. Configure API Keys

Copy and edit `.env` file:

```bash
cp .env.example .env
```

Edit `.env` and set at minimum:

```bash
# Option 1: OpenAI
OPENAI_API_KEY=sk-...
CI_ARTIFACT_ANALYSIS_MODEL=gpt-4o-mini
AGENT_MODEL=gpt-4o-mini

# Option 2: MiniMax via OpenRouter
MINIMAX_API_KEY=sk-or-v1-...
MINIMAX_BASE_URL=https://openrouter.ai/api/v1
MEMCI_LLM_MODEL=MiniMax-M2.5
CI_ARTIFACT_ANALYSIS_MODEL=openrouter/minimax/minimax-m2.5
AGENT_MODEL=openrouter/minimax/minimax-m2.5

# Optional: Custom paths
BENCHMARK_WORK_DIR=./artifacts
MEMORY_ABLATION_LEVELS=L1+L2+L3
```

### 4. Verify Installation

```bash
# Check imports
python -c "from sweagent.benchmark.ci_memory_plugin import build_hierarchical_memory_bank; print('✓ Memory system ready')"

# Check scripts
python scripts/prepare_ci_repair_bench.py --help

# Check configuration
python -c "from dotenv import load_dotenv; load_dotenv(); import os; print(f'API Model: {os.getenv(\"AGENT_MODEL\")}')"
```

## Step-by-Step Execution

### Step 1: Prepare Dataset

Downloads and splits CI repair benchmark into memory seed (~30%) and evaluation (~70%):

```bash
python scripts/prepare_ci_repair_bench.py \
  --dataset ci-benchmark-user/ci-repair-bench \
  --split train \
  --repo-preset core_ci_repair \
  --memory-ratio 0.3 \
  --min-memory-per-repo 5 \
  --output-dir ./artifacts/ci_repair_split
```

**Output**:
- `memory_seed_rows.jsonl` - Seed data for memory construction
- `eval_rows.jsonl` - Evaluation data for benchmarking
- `split_summary.json` - Statistics

**Time**: ~2 minutes

### Step 2: Analyze CI Artifacts

Extracts structured problem documents from CI logs and workflow YAML:

```bash
# Analyze seed rows
python scripts/build_ci_problem_documents.py \
  --input-file ./artifacts/ci_repair_split/memory_seed_rows.jsonl \
  --output-file ./artifacts/ci_repair_split/memory_seed_analyzed.jsonl \
  --mode heuristic

# Analyze evaluation rows
python scripts/build_ci_problem_documents.py \
  --input-file ./artifacts/ci_repair_split/eval_rows.jsonl \
  --output-file ./artifacts/ci_repair_split/eval_analyzed.jsonl \
  --mode heuristic
```

**Modes**:
- `heuristic` - Rule-based, no API calls, ~5 sec/row
- `llm` - LLM-based, more accurate, requires API, ~30 sec/row

**Output**: Rows enriched with `ci_problem_document` and `ci_structured_context`

**Time**: ~5 minutes with heuristic

### Step 3: Build Memory Bank

Constructs hierarchical memory (L1 file-level, L2 repo-level, L3 cross-repo):

```bash
python scripts/build_ci_repair_memory_bank.py \
  --seed-file ./artifacts/ci_repair_split/memory_seed_analyzed.jsonl \
  --output-file ./artifacts/ci_repair_split/memory_bank.json
```

**Output**: 
- `memory_bank.json` containing:
  - `l1`: ~1000s file-level records
  - `l2`: ~100s repo-level patterns
  - `l3`: ~50s cross-repo patterns

**Time**: <1 minute

### Step 4: Build Benchmark Instances

Creates SWE-agent instances for baseline (no memory) and memory versions:

```bash
# BASELINE: No memory injection
python scripts/build_ci_repair_instances.py \
  --eval-file ./artifacts/ci_repair_split/eval_analyzed.jsonl \
  --output-file ./artifacts/ci_repair_split/instances_baseline.json \
  --memory-mode baseline

# MEMORY: With hierarchical retrieval
python scripts/build_ci_repair_instances.py \
  --eval-file ./artifacts/ci_repair_split/eval_analyzed.jsonl \
  --output-file ./artifacts/ci_repair_split/instances_memory.json \
  --memory-mode memory \
  --memory-bank ./artifacts/ci_repair_split/memory_bank.json \
  --top-k 3 \
  --ablation-levels L1+L2+L3 \
  --retrieval-report ./artifacts/ci_repair_split/retrieval_report.json
```

**Output**:
- `instances_baseline.json` - Instances without memory context
- `instances_memory.json` - Instances with retrieved memory
- `retrieval_report.json` - Memory retrieval statistics per instance

**Time**: <1 minute

### Step 5: Run SWE-Agent (Baseline)

Executes repair agent without memory:

```bash
sweagent run batch \
  --config-file config/benchmarks/ci_repair_memory.yaml \
  --instances-file ./artifacts/ci_repair_split/instances_baseline.json \
  --output-dir ./artifacts/ci_repair_split/runs/baseline \
  --suffix "_baseline"
```

**Configuration**: `config/benchmarks/ci_repair_memory.yaml` specifies:
- Agent templates
- Tool bundles (edit, bash, file operations)
- Registry variables
- History processors

**Output**: `runs/baseline/` with:
- `*.md` - Execution trajectories
- `*.info.json` - Summary per instance

**Time**: 1-5 minutes per instance × N instances (could be 1-10 hours for full eval set)

### Step 6: Run SWE-Agent (Memory)

Executes repair agent WITH memory context injected:

```bash
sweagent run batch \
  --config-file config/benchmarks/ci_repair_memory.yaml \
  --instances-file ./artifacts/ci_repair_split/instances_memory.json \
  --output-dir ./artifacts/ci_repair_split/runs/memory \
  --suffix "_memory"
```

**Key Difference**: Each instance has memory context injected in prompt:
```
<retrieved_memory>
=== File-Level Fix ===
...
=== Repo-Level Pattern ===
...
</retrieved_memory>
```

**Time**: Same as baseline (1-5 min per instance)

### Step 7: Evaluate Results

Compare baseline vs. memory performance:

```bash
# Create evaluation script or use results
python -c "
import json
import os
from pathlib import Path

baseline_dir = Path('./artifacts/ci_repair_split/runs/baseline')
memory_dir = Path('./artifacts/ci_repair_split/runs/memory')

def count_successes(run_dir):
    count = 0
    for info_file in run_dir.glob('*.info.json'):
        try:
            data = json.loads(info_file.read_text())
            if data.get('success', False):
                count += 1
        except: pass
    return count

baseline_success = count_successes(baseline_dir)
memory_success = count_successes(memory_dir)
total = max(
    len(list(baseline_dir.glob('*.info.json'))),
    len(list(memory_dir.glob('*.info.json')))
)

print(f'Baseline Success Rate: {baseline_success}/{total} ({100*baseline_success/total:.1f}%)')
print(f'Memory Success Rate: {memory_success}/{total} ({100*memory_success/total:.1f}%)')
print(f'Improvement: +{memory_success - baseline_success} instances ({100*(memory_success-baseline_success)/total:.1f}%)')
"
```

## Automated Full Pipeline

We provide a script to run everything automatically:

```bash
bash scripts/run_full_benchmark.sh
```

This script:
1. ✓ Prepares dataset
2. ✓ Analyzes CI artifacts  
3. ✓ Builds memory bank
4. ✓ Creates instances (baseline + memory)
5. ✓ Runs SWE-agent (both versions)
6. ✓ Generates results report

**Total Time**: 2-12 hours (depending on eval set size and API rate limits)

## Configuration Customization

### Reduce Runtime (Quick Testing)

```bash
# Test with only 10 eval instances
python scripts/prepare_ci_repair_bench.py \
  --memory-ratio 0.5 \
  --output-dir ./artifacts/ci_repair_split_test

python -c "
import json
with open('./artifacts/ci_repair_split_test/eval_rows.jsonl') as f:
    rows = [json.loads(line) for line in f][:10]
with open('./artifacts/ci_repair_split_test/eval_rows_sample.jsonl', 'w') as f:
    for row in rows:
        f.write(json.dumps(row) + '\n')
"
```

### Customize Memory Levels (Ablation)

```bash
# Test L1 only (file-level)
python scripts/build_ci_repair_instances.py \
  --eval-file ./artifacts/ci_repair_split/eval_analyzed.jsonl \
  --output-file ./artifacts/ci_repair_split/instances_l1.json \
  --memory-mode memory \
  --memory-bank ./artifacts/ci_repair_split/memory_bank.json \
  --ablation-levels L1

# Test L1+L2 (file + repo)
python scripts/build_ci_repair_instances.py \
  --eval-file ./artifacts/ci_repair_split/eval_analyzed.jsonl \
  --output-file ./artifacts/ci_repair_split/instances_l1l2.json \
  --memory-mode memory \
  --memory-bank ./artifacts/ci_repair_split/memory_bank.json \
  --ablation-levels L1+L2
```

### Use LLM-Based Analysis (Higher Quality)

```bash
export CI_ARTIFACT_ANALYSIS_MODEL=gpt-4o

python scripts/build_ci_problem_documents.py \
  --input-file ./artifacts/ci_repair_split/eval_rows.jsonl \
  --output-file ./artifacts/ci_repair_split/eval_analyzed_llm.jsonl \
  --mode llm
```

### Custom Docker Deployment

```bash
python scripts/build_ci_repair_instances.py \
  --eval-file ./artifacts/ci_repair_split/eval_analyzed.jsonl \
  --output-file ./artifacts/ci_repair_split/instances_docker.json \
  --memory-mode memory \
  --memory-bank ./artifacts/ci_repair_split/memory_bank.json \
  --deployment-type docker \
  --deployment-image python:3.11-slim
```

## Monitoring & Debugging

### Watch Progress

```bash
# Monitor memory bank
watch -n 5 'ls -lh ./artifacts/ci_repair_split/memory_bank.json'

# Count completed runs
watch -n 5 'find ./artifacts/ci_repair_split/runs -name "*.info.json" | wc -l'

# Stream agent logs (if available)
tail -f ./artifacts/ci_repair_split/runs/memory/*.log
```

### Inspect Results

```bash
# View sample retrieval report
python -c "
import json
with open('./artifacts/ci_repair_split/retrieval_report.json') as f:
    report = json.load(f)
    print(json.dumps(report[0], indent=2))
" | head -100

# Check memory usage
python -c "
import json
with open('./artifacts/ci_repair_split/instances_memory.json') as f:
    instances = json.load(f)
    mem = instances[0]['problem_statement']['extra_fields']['memory_context']
    print(f'Memory context length: {len(mem)} chars')
    print('Sample:')
    print(mem[:500])
"
```

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `ImportError: cannot import name 'build_hierarchical_memory_bank'` | Run `pip install -e .` in repo root |
| `API rate limit exceeded` | Use `heuristic` mode or increase delays |
| `Out of memory during SWE-agent` | Reduce eval set size or use smaller model |
| `HuggingFace dataset not found` | Check internet connection or HF token (`huggingface-cli login`) |
| `Path not found errors` | Set `BENCHMARK_WORK_DIR` env var or use absolute paths |

## Next Steps

1. ✅ Run local benchmark and understand memory impact
2. 🔄 Integrate into CI/CD pipeline (see [CI_INTEGRATION.md](CI_INTEGRATION.md))
3. 📊 Analyze results and refine memory configuration
4. 🔗 Review [ALGORITHM.md](ALGORITHM.md) for technical details
