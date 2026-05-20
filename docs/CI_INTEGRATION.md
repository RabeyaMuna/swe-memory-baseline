# CI Integration Guide: Running SWE-Memory CI Repair Benchmark

## Overview

This guide covers running the SWE-Memory CI repair system in CI/CD pipelines (GitHub Actions, GitLab CI, etc.) to evaluate how memory improves SWE-agent's ability to fix CI failures.

## Quick Start (Local or CI)

### 1. Setup Environment

```bash
# Clone repository
git clone https://github.com/RabeyaMuna/swe-memory-baseline.git
cd swe-memory-baseline

# Create virtual environment
python3.11 -m venv venv
source venv/bin/activate  # or: venv\Scripts\activate on Windows

# Install dependencies
pip install -e ".[dev]"

# Copy environment configuration
cp .env.example .env
# Edit .env with your API keys and paths
```

### 2. Configure Environment Variables

Edit `.env` file with:
```bash
# Required: LLM API keys
MINIMAX_API_KEY=sk-or-v1-...
CI_ARTIFACT_ANALYSIS_MODEL=openrouter/minimax/minimax-m2.5
AGENT_MODEL=openrouter/minimax/minimax-m2.5

# Optional: Custom paths (defaults shown)
BENCHMARK_WORK_DIR=./artifacts
CI_MEMORY_SEED_RATIO=0.3
MEMORY_ABLATION_LEVELS=L1+L2+L3
```

### 3. Run Full Benchmark Pipeline

```bash
# Step 1: Prepare dataset (split into seed and eval)
python scripts/prepare_ci_repair_bench.py \
  --dataset ci-benchmark-user/ci-repair-bench \
  --split train \
  --repo-preset core_ci_repair \
  --memory-ratio 0.3 \
  --min-memory-per-repo 5 \
  --output-dir artifacts/ci_repair_split

# Step 2: Build CI problem documents (analyze logs + workflow)
python scripts/build_ci_problem_documents.py \
  --input-file artifacts/ci_repair_split/memory_seed_rows.jsonl \
  --output-file artifacts/ci_repair_split/memory_seed_analyzed.jsonl \
  --mode heuristic

python scripts/build_ci_problem_documents.py \
  --input-file artifacts/ci_repair_split/eval_rows.jsonl \
  --output-file artifacts/ci_repair_split/eval_analyzed.jsonl \
  --mode heuristic

# Step 3: Build memory bank (construct L1, L2, L3 hierarchy)
python scripts/build_ci_repair_memory_bank.py \
  --seed-file artifacts/ci_repair_split/memory_seed_analyzed.jsonl \
  --output-file artifacts/ci_repair_split/memory_bank.json

# Step 4a: Build instances WITHOUT memory (baseline)
python scripts/build_ci_repair_instances.py \
  --eval-file artifacts/ci_repair_split/eval_analyzed.jsonl \
  --output-file artifacts/ci_repair_split/instances_baseline.json \
  --memory-mode baseline

# Step 4b: Build instances WITH memory (memory group)
python scripts/build_ci_repair_instances.py \
  --eval-file artifacts/ci_repair_split/eval_analyzed.jsonl \
  --output-file artifacts/ci_repair_split/instances_memory.json \
  --memory-mode memory \
  --memory-bank artifacts/ci_repair_split/memory_bank.json \
  --top-k 3 \
  --ablation-levels L1+L2+L3 \
  --retrieval-report artifacts/ci_repair_split/retrieval_report.json

# Step 5: Run SWE-agent on baseline instances
sweagent run batch \
  --config-file config/benchmarks/ci_repair_memory.yaml \
  --environment-setup-cmd "pip install -q e ." \
  --instances-file artifacts/ci_repair_split/instances_baseline.json \
  --output-dir artifacts/ci_repair_split/runs/baseline

# Step 6: Run SWE-agent on memory instances
sweagent run batch \
  --config-file config/benchmarks/ci_repair_memory.yaml \
  --environment-setup-cmd "pip install -q -e ." \
  --instances-file artifacts/ci_repair_split/instances_memory.json \
  --output-dir artifacts/ci_repair_split/runs/memory

# Step 7: Evaluate results
python scripts/evaluate_benchmark.py \
  --baseline-dir artifacts/ci_repair_split/runs/baseline \
  --memory-dir artifacts/ci_repair_split/runs/memory \
  --output-report artifacts/ci_repair_split/benchmark_results.json
```

## Docker Deployment

### Running in Docker Container

```bash
# Build Docker image
docker build -t swe-memory-ci-repair:latest .

# Run benchmark in container
docker run --rm \
  -e MINIMAX_API_KEY=$MINIMAX_API_KEY \
  -e DEPLOYMENT_TYPE=docker \
  -v $(pwd)/artifacts:/workspace/artifacts \
  swe-memory-ci-repair:latest \
  bash scripts/run_full_benchmark.sh
```

### Dockerfile Example

```dockerfile
FROM python:3.11-slim

WORKDIR /workspace

# Install system dependencies
RUN apt-get update && apt-get install -y \
  git curl wget ssh rsync \
  && rm -rf /var/lib/apt/lists/*

# Copy project
COPY . .

# Install Python dependencies
RUN pip install --no-cache-dir -e ".[dev]"

# Create artifacts directory
RUN mkdir -p artifacts

# Set environment
ENV BENCHMARK_WORK_DIR=/workspace/artifacts
ENV DEPLOYMENT_TYPE=docker
ENV CI_MODE=true

CMD ["bash", "scripts/run_full_benchmark.sh"]
```

## GitHub Actions Integration

### Workflow Configuration (`.github/workflows/benchmark.yml`)

```yaml
name: SWE-Memory CI Repair Benchmark

on:
  push:
    branches: [main, develop]
  schedule:
    # Run weekly benchmark
    - cron: '0 0 * * 0'
  workflow_dispatch:  # Manual trigger

env:
  PYTHON_VERSION: '3.11'
  BENCHMARK_WORK_DIR: ./artifacts
  CI_MODE: true

jobs:
  benchmark:
    runs-on: ubuntu-latest
    timeout-minutes: 480  # 8 hours
    
    steps:
      - uses: actions/checkout@v4
      
      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: ${{ env.PYTHON_VERSION }}
          cache: 'pip'
      
      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -e ".[dev]"
      
      - name: Prepare dataset
        run: |
          python scripts/prepare_ci_repair_bench.py \
            --output-dir ${{ env.BENCHMARK_WORK_DIR }}/ci_repair_split
        env:
          HF_TOKEN: ${{ secrets.HF_TOKEN }}
      
      - name: Analyze CI artifacts
        run: |
          python scripts/build_ci_problem_documents.py \
            --input-file ${{ env.BENCHMARK_WORK_DIR }}/ci_repair_split/memory_seed_rows.jsonl \
            --output-file ${{ env.BENCHMARK_WORK_DIR }}/ci_repair_split/memory_seed_analyzed.jsonl \
            --mode heuristic
          
          python scripts/build_ci_problem_documents.py \
            --input-file ${{ env.BENCHMARK_WORK_DIR }}/ci_repair_split/eval_rows.jsonl \
            --output-file ${{ env.BENCHMARK_WORK_DIR }}/ci_repair_split/eval_analyzed.jsonl \
            --mode heuristic
      
      - name: Build memory bank
        run: |
          python scripts/build_ci_repair_memory_bank.py \
            --seed-file ${{ env.BENCHMARK_WORK_DIR }}/ci_repair_split/memory_seed_analyzed.jsonl \
            --output-file ${{ env.BENCHMARK_WORK_DIR }}/ci_repair_split/memory_bank.json
      
      - name: Build benchmark instances
        run: |
          # Baseline
          python scripts/build_ci_repair_instances.py \
            --eval-file ${{ env.BENCHMARK_WORK_DIR }}/ci_repair_split/eval_analyzed.jsonl \
            --output-file ${{ env.BENCHMARK_WORK_DIR }}/ci_repair_split/instances_baseline.json \
            --memory-mode baseline
          
          # Memory
          python scripts/build_ci_repair_instances.py \
            --eval-file ${{ env.BENCHMARK_WORK_DIR }}/ci_repair_split/eval_analyzed.jsonl \
            --output-file ${{ env.BENCHMARK_WORK_DIR }}/ci_repair_split/instances_memory.json \
            --memory-mode memory \
            --memory-bank ${{ env.BENCHMARK_WORK_DIR }}/ci_repair_split/memory_bank.json
      
      - name: Run SWE-agent (Baseline)
        run: |
          sweagent run batch \
            --config-file config/benchmarks/ci_repair_memory.yaml \
            --instances-file ${{ env.BENCHMARK_WORK_DIR }}/ci_repair_split/instances_baseline.json \
            --output-dir ${{ env.BENCHMARK_WORK_DIR }}/ci_repair_split/runs/baseline
        env:
          AGENT_MODEL: ${{ secrets.AGENT_MODEL }}
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
        timeout-minutes: 240
      
      - name: Run SWE-agent (Memory)
        run: |
          sweagent run batch \
            --config-file config/benchmarks/ci_repair_memory.yaml \
            --instances-file ${{ env.BENCHMARK_WORK_DIR }}/ci_repair_split/instances_memory.json \
            --output-dir ${{ env.BENCHMARK_WORK_DIR }}/ci_repair_split/runs/memory
        env:
          AGENT_MODEL: ${{ secrets.AGENT_MODEL }}
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
        timeout-minutes: 240
      
      - name: Evaluate results
        run: |
          python scripts/evaluate_benchmark.py \
            --baseline-dir ${{ env.BENCHMARK_WORK_DIR }}/ci_repair_split/runs/baseline \
            --memory-dir ${{ env.BENCHMARK_WORK_DIR }}/ci_repair_split/runs/memory \
            --output-report ${{ env.BENCHMARK_WORK_DIR }}/ci_repair_split/benchmark_results.json
      
      - name: Upload artifacts
        if: always()
        uses: actions/upload-artifact@v3
        with:
          name: benchmark-results
          path: artifacts/ci_repair_split/
          retention-days: 30
      
      - name: Comment on PR
        if: github.event_name == 'pull_request'
        uses: actions/github-script@v7
        with:
          script: |
            const fs = require('fs');
            const results = JSON.parse(
              fs.readFileSync('artifacts/ci_repair_split/benchmark_results.json')
            );
            const comment = `
            ## SWE-Memory Benchmark Results
            
            **Baseline**: ${results.baseline_success_rate}% success
            **With Memory**: ${results.memory_success_rate}% success
            **Improvement**: ${results.improvement}%
            
            Full report: [See artifacts](https://github.com/${{ github.repository }}/actions/runs/${{ github.run_id }})
            `;
            github.rest.issues.createComment({
              issue_number: context.issue.number,
              owner: context.repo.owner,
              repo: context.repo.repo,
              body: comment
            });
```

## Ablation Testing in CI

To test different memory levels:

```bash
# L1 only (file-level)
python scripts/build_ci_repair_instances.py \
  --eval-file artifacts/ci_repair_split/eval_analyzed.jsonl \
  --output-file artifacts/ci_repair_split/instances_l1.json \
  --memory-mode memory \
  --memory-bank artifacts/ci_repair_split/memory_bank.json \
  --ablation-levels L1

# L1+L2 (file + repo level)
python scripts/build_ci_repair_instances.py \
  --eval-file artifacts/ci_repair_split/eval_analyzed.jsonl \
  --output-file artifacts/ci_repair_split/instances_l1l2.json \
  --memory-mode memory \
  --memory-bank artifacts/ci_repair_split/memory_bank.json \
  --ablation-levels L1+L2

# L1+L2+L3 (full, default)
python scripts/build_ci_repair_instances.py \
  --eval-file artifacts/ci_repair_split/eval_analyzed.jsonl \
  --output-file artifacts/ci_repair_split/instances_full.json \
  --memory-mode memory \
  --memory-bank artifacts/ci_repair_split/memory_bank.json \
  --ablation-levels L1+L2+L3
```

## Monitoring & Debugging

### Check Pipeline Progress

```bash
# View memory bank statistics
python -c "
import json
with open('artifacts/ci_repair_split/memory_bank.json') as f:
    bank = json.load(f)
    print(f'L1 records: {len(bank[\"l1\"])}')
    print(f'L2 records: {len(bank[\"l2\"])}')
    print(f'L3 records: {len(bank[\"l3\"])}')
"

# Check retrieval report
python -c "
import json
with open('artifacts/ci_repair_split/retrieval_report.json') as f:
    report = json.load(f)
    used = sum(1 for r in report if r['memory_used'])
    print(f'Memory used in {used}/{len(report)} instances')
"

# View sample memory context
python -c "
import json
with open('artifacts/ci_repair_split/instances_memory.json') as f:
    instances = json.load(f)
    print(instances[0]['problem_statement']['extra_fields']['memory_context'][:500])
"
```

### Enable Debug Logging

```bash
export DEBUG_MEMORY_RETRIEVAL=true
export LOG_LEVEL=DEBUG

python scripts/build_ci_repair_instances.py \
  --eval-file artifacts/ci_repair_split/eval_analyzed.jsonl \
  --output-file artifacts/ci_repair_split/instances_memory.json \
  --memory-mode memory \
  --memory-bank artifacts/ci_repair_split/memory_bank.json
```

## Performance Tuning

| Configuration | Impact | Recommendation |
|---------------|--------|-----------------|
| `MEMORY_TOP_K` | More candidates = better recall but slower | Start with 3-5 |
| `CI_MAX_LOG_CHARS` | Larger logs = better context but slower | 12000 is balanced |
| `MEMORY_ABLATION_LEVELS` | L1+L2+L3 best but slowest | Use full for eval |
| `DEPLOYMENT_TYPE` | Docker adds ~2x overhead | Use `local` for speed |

## Troubleshooting

### Issue: Out of Memory

```bash
# Reduce dataset size
python scripts/prepare_ci_repair_bench.py \
  --memory-ratio 0.1 \  # Smaller seed
  --output-dir artifacts/ci_repair_split

# Or sample instances
python -c "
import json
with open('artifacts/ci_repair_split/instances_memory.json') as f:
    instances = json.load(f)
    sample = instances[:100]  # First 100
with open('artifacts/ci_repair_split/instances_memory_sample.json', 'w') as f:
    json.dump(sample, f)
"
```

### Issue: API Rate Limits

```bash
# Use heuristic mode instead of LLM
export CI_ANALYSIS_MODE=heuristic

python scripts/build_ci_problem_documents.py \
  --input-file artifacts/ci_repair_split/eval_rows.jsonl \
  --output-file artifacts/ci_repair_split/eval_analyzed.jsonl \
  --mode heuristic  # Much faster, no API calls
```

### Issue: Hardcoded Paths in Logs

All scripts now support path configuration via environment variables:
```bash
export BENCHMARK_WORK_DIR=/custom/path/artifacts
export SWE_AGENT_CONFIG_ROOT=/path/to/config

# Scripts will respect these directories
```

## Next Steps

1. **Integration**: Add to your CI/CD pipeline
2. **Evaluation**: Compare baseline vs. memory metrics
3. **Customization**: Adjust memory weights, thresholds, repo presets
4. **Analysis**: Review retrieval reports to understand memory contribution
