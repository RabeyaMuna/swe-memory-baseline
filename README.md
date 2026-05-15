# SWE-Memory-Baseline

This repository evaluates a simple claim:

`Does adding retrieval-based memory improve a fixed repair scaffold on recurring CI failures?`

The scaffold is `SWE-agent`. The contribution here is not a new autonomous repair system. The contribution is a thin memory layer on top of the existing scaffold, so the comparison stays:

- `SWE-agent` baseline
- `SWE-agent + memory`

## Purpose

This project is designed for controlled benchmark evaluation on CI repair tasks. The goal is to measure whether prior solved failures, stored as reusable memory artifacts, help `SWE-agent` repair later failures from the same benchmark.

The intended benchmark source is:

- `ci-benchmark-user/ci-repair-bench`

The intended evaluation style is:

1. Split benchmark rows into a memory-seed partition and an evaluation partition.
2. Build memory only from the seed partition.
3. Run `SWE-agent` on the evaluation partition with no memory.
4. Run `SWE-agent` again on the exact same evaluation partition with retrieved memory hints.
5. Compare outcomes.

This matches the methodology requirement that the research contribution is the memory augmentation, not the full repair stack.

## What Is In This Repo

This repo is a forked research workspace built on top of `SWE-agent`, with a small CI-repair benchmark layer added.

New project-specific components:

- [sweagent/benchmark/ci_repair_memory.py](sweagent/benchmark/ci_repair_memory.py)
  dataset adaptation, split logic, memory record construction, retrieval, and prompt-context rendering
- [scripts/prepare_ci_repair_bench.py](scripts/prepare_ci_repair_bench.py)
  creates train/test style partitions from `ci-repair-bench`
- [scripts/build_ci_repair_memory_bank.py](scripts/build_ci_repair_memory_bank.py)
  builds a lightweight reusable memory bank from seed rows
- [scripts/build_ci_repair_instances.py](scripts/build_ci_repair_instances.py)
  produces `SWE-agent` expert instances for baseline and memory runs
- [config/benchmarks/ci_repair_memory.yaml](config/benchmarks/ci_repair_memory.yaml)
  benchmark prompt/config that injects retrieved memory as non-binding hints

## Benchmark Artifacts

The benchmark pipeline produces the following artifacts:

- `artifacts/ci_repair_split/memory_seed_rows.jsonl`
  rows used to build memory
- `artifacts/ci_repair_split/eval_rows.jsonl`
  rows used for evaluation
- `artifacts/ci_repair_split/memory_bank.json`
  reusable memory records derived from seed issues
- `artifacts/ci_repair_split/eval_instances_baseline.json`
  `SWE-agent` instances for the no-memory baseline
- `artifacts/ci_repair_split/eval_instances_memory.json`
  `SWE-agent` instances for the memory-augmented run
- `trajectories/...`
  normal `SWE-agent` run outputs, patches, and trajectories

## Algorithm

The memory mechanism in this repo is intentionally lightweight.

It does not reuse the full multi-agent logic from a separate CI repair project. Instead, it applies only the minimum additions needed for a fair benchmark ablation.

### Step 1: Convert benchmark rows into repair tasks

Each `ci-repair-bench` row is converted into a `SWE-agent` problem statement containing:

- repository identity
- failing commit
- workflow name and path
- changed files
- workflow YAML
- failing log excerpts

The gold diff is used only on the memory-seed side to summarize past successful fixes. It is not exposed to the evaluation prompt for the current test instance.

### Step 2: Build reusable memory records

For each seed issue, the memory builder stores:

- repo
- workflow name and path
- error types
- changed files
- failed commands parsed from logs
- patch file targets extracted from the solved diff
- coarse patch patterns such as `import_edit`, `formatting`, `whitespace`, or `general_code_edit`
- a short fix summary

### Step 3: Retrieve similar prior failures

At evaluation time, each issue retrieves top prior records using a simple weighted score:

- same repo
- overlapping error type
- same workflow name
- overlapping changed files
- overlapping log tokens

Only top matches above a minimum score are kept.

### Step 4: Inject memory into the prompt

Retrieved records are rendered into `memory_context` and inserted into the benchmark prompt as:

- prior repo/workflow context
- prior changed files
- prior failed commands
- prior patch patterns
- a short reusable fix hint

The prompt explicitly tells the model to treat this as non-binding prior experience and ignore it if it conflicts with repository evidence.

## Why This Design

This design is deliberate.

It keeps the research contribution narrow:

- baseline scaffold stays `SWE-agent`
- task instances stay benchmark-derived
- memory is the only new capability under test

This is the right setup for a paper or thesis question of the form:

`Does memory improve an existing software-repair scaffold on recurring CI failures?`

## Setup

### 1. Clone the repository

```bash
git clone git@github.com:RabeyaMuna/swe-memory-baseline.git
cd swe-memory-baseline
```

### 2. Create an environment

Use Python `3.11+`.

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e .
```

If you plan to run the benchmark with Docker-backed instances, also make sure Docker is installed and running.

### 3. Set model credentials

Set the provider keys needed by your chosen `SWE-agent` model configuration. For example:

```bash
export OPENAI_API_KEY=...
export ANTHROPIC_API_KEY=...
export GITHUB_TOKEN=...
```

`GITHUB_TOKEN` is recommended because benchmark instances clone GitHub repositories at specific commits.

## How To Run

### 1. Prepare the benchmark split

This creates a memory-seed partition and an evaluation partition.

```bash
python scripts/prepare_ci_repair_bench.py \
  --dataset ci-benchmark-user/ci-repair-bench \
  --split train \
  --repos huggingface/diffusers canonical/cloud-init agno-agi/agno OpenAccess-AI-Collective/axolotl conan-io/conan flowersteam/flower \
  --memory-ratio 0.3 \
  --min-memory-per-repo 5 \
  --output-dir artifacts/ci_repair_split
```

### 2. Build the memory bank

```bash
python scripts/build_ci_repair_memory_bank.py \
  --seed-file artifacts/ci_repair_split/memory_seed_rows.jsonl \
  --output-file artifacts/ci_repair_split/memory_bank.json
```

### 3. Build baseline instances

```bash
python scripts/build_ci_repair_instances.py \
  --eval-file artifacts/ci_repair_split/eval_rows.jsonl \
  --output-file artifacts/ci_repair_split/eval_instances_baseline.json
```

### 4. Build memory-augmented instances

```bash
python scripts/build_ci_repair_instances.py \
  --eval-file artifacts/ci_repair_split/eval_rows.jsonl \
  --memory-bank artifacts/ci_repair_split/memory_bank.json \
  --output-file artifacts/ci_repair_split/eval_instances_memory.json \
  --top-k 3 \
  --min-score 0.15
```

### 5. Run the baseline

```bash
sweagent run-batch \
  --config config/benchmarks/ci_repair_memory.yaml \
  --instances.type expert_file \
  --instances.path artifacts/ci_repair_split/eval_instances_baseline.json
```

### 6. Run the memory version

```bash
sweagent run-batch \
  --config config/benchmarks/ci_repair_memory.yaml \
  --instances.type expert_file \
  --instances.path artifacts/ci_repair_split/eval_instances_memory.json
```

## Evaluation Logic

The clean comparison is:

- same model
- same agent scaffold
- same benchmark rows
- same prompt template except for memory content
- same budget and runtime settings

Only the presence or absence of retrieved memory should change.

Recommended reported metrics:

- repair success rate
- number of submitted patches
- retrieval hit rate
- retrieval precision
- per-repo baseline vs memory deltas

## Notes On Data Leakage

To keep the benchmark valid:

- do not put evaluation rows into the memory bank before testing them
- do not expose the evaluation row’s gold diff to the model
- build memory only from the seed partition
- keep the split deterministic and documented

## Relation To The Separate CI Repair Project

This repository should not become a copy of a larger custom CI repair system.

That larger system may contain:

- custom fault localization agents
- environment setup agents
- retry loops
- task-specific orchestration

Those are useful engineering components, but they are not appropriate if the benchmark claim is specifically about memory augmentation of an existing scaffold.

This repo therefore keeps only the thin parts needed for:

- benchmark adaptation
- memory construction
- memory retrieval
- prompt injection

## Upstream

This work is built on top of `SWE-agent`.

Original project:

- https://github.com/SWE-agent/SWE-agent

If you use this repository, cite both:

- the upstream `SWE-agent` paper
- your own benchmark-memory study

## License

This repository inherits the upstream MIT license. See [LICENSE](LICENSE).
