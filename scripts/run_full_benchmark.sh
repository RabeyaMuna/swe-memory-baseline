#!/bin/bash
# Full CI Repair Benchmark Pipeline
# Runs all steps: prepare data, build memory, create instances, run agent, evaluate

set -e

# Color output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Load environment variables
if [ -f .env ]; then
    export $(cat .env | grep -v '^#' | xargs)
    log_info "Loaded configuration from .env"
else
    log_warn ".env file not found. Using defaults."
fi

# Use environment variables or defaults
BENCHMARK_WORK_DIR=${BENCHMARK_WORK_DIR:-./artifacts}
OUTPUT_DIR=${BENCHMARK_WORK_DIR}/ci_repair_split
DATASET=${CI_BENCHMARK_DATASET:-ci-benchmark-user/ci-repair-bench}
SPLIT=${CI_BENCHMARK_SPLIT:-train}
REPO_PRESET=${CI_REPAIR_REPO_PRESET:-core_ci_repair}
MEMORY_RATIO=${CI_MEMORY_SEED_RATIO:-0.3}
MIN_MEMORY=${CI_MIN_MEMORY_PER_REPO:-5}
ANALYSIS_MODE=${CI_ANALYSIS_MODE:-heuristic}
MEMORY_MODE=${MEMORY_MODE:-memory}
MEMORY_TOP_K=${MEMORY_TOP_K:-3}
ABLATION=${MEMORY_ABLATION_LEVELS:-L1+L2+L3}

log_info "=== SWE-Memory CI Repair Benchmark ==="
log_info "Workspace: ${BENCHMARK_WORK_DIR}"
log_info "Dataset: ${DATASET} (${SPLIT})"
log_info "Preset: ${REPO_PRESET}"
log_info "Memory Ratio: ${MEMORY_RATIO}"
log_info "Memory Mode: ${MEMORY_MODE}"
log_info "Ablation: ${ABLATION}"
echo ""

# Step 1: Prepare dataset
log_info "Step 1: Preparing dataset..."
python scripts/prepare_ci_repair_bench.py \
  --dataset "${DATASET}" \
  --split "${SPLIT}" \
  --repo-preset "${REPO_PRESET}" \
  --memory-ratio "${MEMORY_RATIO}" \
  --min-memory-per-repo "${MIN_MEMORY}" \
  --output-dir "${OUTPUT_DIR}"

log_info "✓ Dataset split complete"
SEED_COUNT=$(wc -l < "${OUTPUT_DIR}/memory_seed_rows.jsonl")
EVAL_COUNT=$(wc -l < "${OUTPUT_DIR}/eval_rows.jsonl")
log_info "  Seed rows: ${SEED_COUNT}"
log_info "  Eval rows: ${EVAL_COUNT}"
echo ""

# Step 2: Analyze CI artifacts (seed)
log_info "Step 2a: Analyzing seed CI artifacts (${ANALYSIS_MODE})..."
python scripts/build_ci_problem_documents.py \
  --input-file "${OUTPUT_DIR}/memory_seed_rows.jsonl" \
  --output-file "${OUTPUT_DIR}/memory_seed_analyzed.jsonl" \
  --mode "${ANALYSIS_MODE}"
log_info "✓ Seed analysis complete"

# Step 2b: Analyze CI artifacts (eval)
log_info "Step 2b: Analyzing eval CI artifacts (${ANALYSIS_MODE})..."
python scripts/build_ci_problem_documents.py \
  --input-file "${OUTPUT_DIR}/eval_rows.jsonl" \
  --output-file "${OUTPUT_DIR}/eval_analyzed.jsonl" \
  --mode "${ANALYSIS_MODE}"
log_info "✓ Eval analysis complete"
echo ""

# Step 3: Build memory bank
log_info "Step 3: Building hierarchical memory bank..."
python scripts/build_ci_repair_memory_bank.py \
  --seed-file "${OUTPUT_DIR}/memory_seed_analyzed.jsonl" \
  --output-file "${OUTPUT_DIR}/memory_bank.json"

log_info "✓ Memory bank built"
python -c "
import json
with open('${OUTPUT_DIR}/memory_bank.json') as f:
    bank = json.load(f)
    print('  L1 (file-level): ' + str(len(bank.get('l1', []))) + ' records')
    print('  L2 (repo-level): ' + str(len(bank.get('l2', []))) + ' records')
    print('  L3 (cross-repo): ' + str(len(bank.get('l3', []))) + ' records')
"
echo ""

# Step 4: Build instances (baseline)
log_info "Step 4a: Building baseline instances (no memory)..."
python scripts/build_ci_repair_instances.py \
  --eval-file "${OUTPUT_DIR}/eval_analyzed.jsonl" \
  --output-file "${OUTPUT_DIR}/instances_baseline.json" \
  --memory-mode baseline
log_info "✓ Baseline instances created"

# Step 4b: Build instances (memory)
log_info "Step 4b: Building memory instances..."
python scripts/build_ci_repair_instances.py \
  --eval-file "${OUTPUT_DIR}/eval_analyzed.jsonl" \
  --output-file "${OUTPUT_DIR}/instances_memory.json" \
  --memory-mode memory \
  --memory-bank "${OUTPUT_DIR}/memory_bank.json" \
  --top-k "${MEMORY_TOP_K}" \
  --ablation-levels "${ABLATION}" \
  --retrieval-report "${OUTPUT_DIR}/retrieval_report.json"
log_info "✓ Memory instances created"

# Analyze retrieval effectiveness
python -c "
import json
with open('${OUTPUT_DIR}/retrieval_report.json') as f:
    report = json.load(f)
    memory_used = sum(1 for r in report if r.get('memory_used', False))
    print('  Memory used in: ' + str(memory_used) + '/' + str(len(report)) + ' instances')
    if memory_used > 0:
        avg_score = sum(r.get('weighted_similarity', 0) for r in report) / len(report)
        print('  Avg similarity score: {:.3f}'.format(avg_score))
"
echo ""

# Step 5a: Run SWE-agent (baseline)
log_info "Step 5a: Running SWE-agent on baseline instances..."
log_warn "This may take 1-10 hours depending on eval set size"

sweagent run batch \
  --config-file config/benchmarks/ci_repair_memory.yaml \
  --instances-file "${OUTPUT_DIR}/instances_baseline.json" \
  --output-dir "${OUTPUT_DIR}/runs/baseline"

log_info "✓ Baseline run complete"

# Step 5b: Run SWE-agent (memory)
log_info "Step 5b: Running SWE-agent on memory instances..."

sweagent run batch \
  --config-file config/benchmarks/ci_repair_memory.yaml \
  --instances-file "${OUTPUT_DIR}/instances_memory.json" \
  --output-dir "${OUTPUT_DIR}/runs/memory"

log_info "✓ Memory run complete"
echo ""

# Step 6: Evaluate results
log_info "Step 6: Evaluating results..."

python -c "
import json
from pathlib import Path

def count_successes(run_dir):
    count = 0
    total = 0
    for info_file in Path(run_dir).glob('*.info.json'):
        try:
            total += 1
            data = json.loads(info_file.read_text())
            if data.get('success', False):
                count += 1
        except:
            pass
    return count, total

baseline_success, baseline_total = count_successes('${OUTPUT_DIR}/runs/baseline')
memory_success, memory_total = count_successes('${OUTPUT_DIR}/runs/memory')

baseline_rate = 100 * baseline_success / baseline_total if baseline_total > 0 else 0
memory_rate = 100 * memory_success / memory_total if memory_total > 0 else 0
improvement = memory_rate - baseline_rate

print(f'Baseline Success Rate: {baseline_success}/{baseline_total} ({baseline_rate:.1f}%)')
print(f'Memory Success Rate: {memory_success}/{memory_total} ({memory_rate:.1f}%)')
print(f'Improvement: {improvement:+.1f}%')

results = {
    'baseline_success': baseline_success,
    'baseline_total': baseline_total,
    'baseline_rate': baseline_rate,
    'memory_success': memory_success,
    'memory_total': memory_total,
    'memory_rate': memory_rate,
    'improvement_rate': improvement,
    'ablation': '${ABLATION}',
    'memory_top_k': ${MEMORY_TOP_K},
}
with open('${OUTPUT_DIR}/benchmark_results.json', 'w') as f:
    json.dump(results, f, indent=2)
"

log_info "✓ Results saved to ${OUTPUT_DIR}/benchmark_results.json"
echo ""

log_info "=== Benchmark Complete ==="
log_info "Results available at: ${OUTPUT_DIR}/"
log_info "Key artifacts:"
log_info "  - memory_bank.json: Hierarchical memory (L1, L2, L3)"
log_info "  - retrieval_report.json: Memory retrieval statistics"
log_info "  - runs/baseline/: Baseline agent execution logs"
log_info "  - runs/memory/: Memory-augmented agent execution logs"
log_info "  - benchmark_results.json: Summary statistics"
