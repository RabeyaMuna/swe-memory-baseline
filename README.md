# SWE-Memory-Baseline

This repository evaluates one narrow research question:

`Does adding CI-aware retrieval memory improve a fixed SWE-agent scaffold on recurring CI failures?`

The contribution here is not a brand-new CI repair system. The contribution is a memory layer added on top of an existing scaffold.

The comparison is:

- `SWE-agent` baseline
- `SWE-agent + memory`

## Research Goal

The professor-aligned setup is:

1. use an existing agent scaffold
2. keep that scaffold fixed
3. add memory/retrieval on top
4. run the same benchmark tasks with and without memory
5. compare the results

This keeps the claim clean:

- the scaffold is not the novelty
- the memory system is the novelty

## Benchmark Source

The benchmark data comes from:

- `ci-benchmark-user/ci-repair-bench`

Each row contains CI-specific artifacts such as:

- `repo_owner`, `repo_name`
- `workflow_name`, `workflow_path`, `workflow`
- `sha_fail`, `sha_success`
- `logs`
- `diff`
- `changed_files`
- `error_type`

## High-Level Pipeline

The full pipeline is:

1. split benchmark rows into `memory seed` and `evaluation`
2. analyze `workflow + CI logs` into a clean CI problem document
3. build a memory bank from seed rows only
4. build `SWE-agent` instances for:
   - baseline: no memory
   - memory: retrieved prior context added
5. run `SWE-agent` on the same eval rows twice
6. compare outcomes

## What Memory Means Here

Memory is used as a retrieval layer on top of `SWE-agent`.

It does not replace the `SWE-agent` loop. It does not add a custom multi-agent repair runtime inside the scaffold.

Memory is used to retrieve prior solved CI context such as:

- similar prior failures
- recurring repo-level patterns
- cross-repo general repair patterns
- likely files, tools, commands, and fix styles

That retrieved information is rendered into `memory_context` and injected into the prompt.

## What Is Shared Between Baseline And Memory Runs

Both runs use the same:

- benchmark rows
- cleaned CI problem document
- scaffold
- model
- prompt structure
- runtime budget

Only one thing changes:

- baseline: `memory_context = ""`
- memory run: `memory_context = retrieved prior experience`

## Repository Components

Main benchmark-specific files:

- [scripts/prepare_ci_repair_bench.py](scripts/prepare_ci_repair_bench.py)
- [scripts/build_ci_problem_documents.py](scripts/build_ci_problem_documents.py)
- [scripts/build_ci_repair_memory_bank.py](scripts/build_ci_repair_memory_bank.py)
- [scripts/build_ci_repair_instances.py](scripts/build_ci_repair_instances.py)
- [sweagent/benchmark/ci_problem_document.py](sweagent/benchmark/ci_problem_document.py)
- [sweagent/benchmark/ci_memory_plugin.py](sweagent/benchmark/ci_memory_plugin.py)
- [sweagent/benchmark/ci_repair_memory.py](sweagent/benchmark/ci_repair_memory.py)
- [config/benchmarks/ci_repair_memory.yaml](config/benchmarks/ci_repair_memory.yaml)

## Script-By-Script Algorithm

### 1. `scripts/prepare_ci_repair_bench.py`

Purpose:
- load the Hugging Face dataset
- filter to target repos
- split rows into memory seed and evaluation partitions

Algorithm:
1. load dataset rows from `ci-benchmark-user/ci-repair-bench`
2. keep only the selected repos
3. group rows by repo
4. deterministically split each repo using:
   - `memory_ratio`
   - `min_memory_per_repo`
5. write:
   - `memory_seed_rows.jsonl`
   - `eval_rows.jsonl`
   - `split_summary.json`

Why it exists:
- avoids leakage
- ensures memory is built only from past seed rows

### 2. `scripts/build_ci_problem_documents.py`

Purpose:
- convert noisy CI artifacts into a clean problem document

Input:
- raw benchmark rows containing workflow YAML and logs

Output:
- analyzed rows with:
  - `ci_problem_document`
  - `ci_structured_context`

Algorithm:
1. load each benchmark row
2. read `workflow` and `logs`
3. split logs into chunks
4. analyze each chunk separately
5. extract chunk-level signals:
   - `chunk_summary`
   - `mentioned_tokens`
   - `effected_files`
   - `failed_jobs`
6. merge chunk outputs into one structured failure summary
7. build one final `problem_document`

The final document includes sections such as:

- `Workflow Context`
- `Likely Failing Commands`
- `Affected Files`
- `Failed Jobs`
- `Failure Summary`
- `Mentioned Tokens`
- `Workflow YAML`
- `Chunked Failure Summary`

Modes:

- `--mode heuristic`
  - no model call
  - deterministic local extraction
- `--mode llm`
  - uses the configured model to analyze chunks and merge them

Why it exists:
- raw CI logs are too noisy for direct prompting
- both baseline and memory runs need the same cleaned task representation

### 3. `scripts/build_ci_repair_memory_bank.py`

Purpose:
- build reusable memory only from the seed side

Input:
- `memory_seed_rows_analyzed.jsonl`

Output:
- `memory_bank.json`

Algorithm:
1. load analyzed seed rows
2. convert each row into memory records
3. organize memory into three levels:
   - `L1`: direct prior failure instances
   - `L2`: repo-level semantic recurring patterns
   - `L3`: cross-repo semantic generalized patterns
4. write a single hierarchical memory bank

Why it exists:
- captures prior solved CI experience in a reusable retrieval format

### 4. `scripts/build_ci_repair_instances.py`

Purpose:
- create final `SWE-agent` input instances

Input:
- analyzed eval rows
- optional memory bank

Output:
- `eval_instances_baseline.json`
- or `eval_instances_memory.json`

Algorithm for baseline:
1. load analyzed eval rows
2. use `ci_problem_document` as `problem_statement`
3. set `memory_context` to empty
4. write `expert_file` instances

Algorithm for memory run:
1. load analyzed eval rows
2. load `memory_bank.json`
3. compute layered similarity against `L1/L2/L3`
4. select matches above per-layer thresholds
5. render retrieved evidence, score breakdowns, and match rationale into `memory_context`
6. write `expert_file` instances

Why it exists:
- creates the exact files that `SWE-agent` consumes
- isolates baseline vs memory at the prompt-input level

## Memory Retrieval Algorithm

The memory system here is intentionally lightweight and benchmark-focused.

It is hierarchical, and each layer has a different retrieval role.

### L1

Direct prior-instance matching inside the same repository.

Purpose:

- find very close prior failures
- prioritize precision over recall

Signals used:

- same repo
- file overlap
- error type overlap
- failed job overlap
- text similarity
- failure-reason similarity

Current weighted score:

```text
L1 =
  0.30 * file_overlap +
  0.20 * error_type_match +
  0.20 * text_similarity +
  0.15 * failed_job_match +
  0.15 * failure_reason_similarity
```

Interpretation:

- `L1` is the strongest “we have seen this almost directly before” layer

### L2

Repo-level semantic recurring patterns.

Purpose:

- find recurring failure patterns within the same repo
- allow wording differences while keeping repo context fixed

Signals used:

- same repo
- cosine-style semantic similarity over normalized retrieval documents
- workflow similarity
- error type overlap
- text similarity
- file-pattern similarity

Current weighted score:

```text
L2 =
  0.45 * cosine_similarity +
  0.20 * workflow_similarity +
  0.15 * error_type_match +
  0.10 * text_similarity +
  0.10 * file_pattern_similarity
```

Interpretation:

- `L2` answers: “Have we seen a semantically similar recurring pattern in this repo before?”

### L3

Cross-repo semantic generalized patterns.

Purpose:

- find similar root-cause patterns across different repositories
- provide reusable but weaker prior experience

Signals used:

- cosine-style semantic similarity over normalized retrieval documents
- error type overlap
- tool similarity
- text similarity

Current weighted score:

```text
L3 =
  0.55 * cosine_similarity +
  0.20 * error_type_match +
  0.15 * tool_similarity +
  0.10 * text_similarity
```

Interpretation:

- `L3` answers: “Have we seen this root-cause pattern anywhere before?”

### Retrieval Documents

For semantic matching, the system builds a normalized retrieval document from:

- repo
- workflow name/path
- error types
- failed jobs
- failed commands
- affected files
- failure reasons
- mentioned tokens
- organized log summary
- compact CI problem document text

These retrieval documents are used for cosine-style semantic similarity in `L2` and `L3`.

### Layer Combination

### Retrieval Output

The retriever produces:

- selected `L1/L2/L3` matches
- similarity scores
- per-match score breakdowns
- per-match retrieval rationale
- weighted combined similarity
- `llm_similarity_candidates`
- rendered `memory_context`

The current combined layer score is:

```text
weighted_similarity =
  0.60 * best_L1 +
  0.25 * best_L2 +
  0.15 * best_L3
```

This combined score is used only to summarize overall retrieval strength. The prompt still preserves the individual layer outputs.

### Thresholds

The current implementation uses per-layer minimum thresholds:

- `L1 >= 0.15`
- `L2 >= 0.20`
- `L3 >= 0.20`

These are initial benchmark thresholds and should still be tuned and justified experimentally in the final evaluation.

### LLM Use Of Retrieved Memory

The retriever does not yet run a separate post-retrieval LLM reranking call.

Instead, it prepares `memory_context` so the repair model can interpret:

- direct `L1` prior cases
- repo-level `L2` semantic patterns
- cross-repo `L3` semantic patterns
- score breakdowns
- retrieval rationale

So the current flow is:

1. retrieve by layered similarity
2. inject retrieved evidence into `memory_context`
3. let `SWE-agent` use that evidence during repair

The `memory_context` is advisory only. The prompt explicitly tells `SWE-agent` to ignore retrieved memory if it conflicts with repository evidence.

## Artifacts Produced

The pipeline writes:

- `artifacts/ci_repair_split/memory_seed_rows.jsonl`
- `artifacts/ci_repair_split/eval_rows.jsonl`
- `artifacts/ci_repair_split/split_summary.json`
- `artifacts/ci_repair_split/memory_seed_rows_analyzed.jsonl`
- `artifacts/ci_repair_split/eval_rows_analyzed.jsonl`
- `artifacts/ci_repair_split/memory_bank.json`
- `artifacts/ci_repair_split/eval_instances_baseline.json`
- `artifacts/ci_repair_split/eval_instances_memory.json`
- `artifacts/ci_repair_split/retrieval_report.json`
- `trajectories/...`

## Setup

### 1. Clone

```bash
git clone git@github.com:RabeyaMuna/swe-memory-baseline.git
cd swe-memory-baseline
```

### 2. Create Environment

Use Python `3.11+`.

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e .
```

If you will run repository environments through Docker, make sure Docker is installed and running.

### 3. Create `.env`

Copy the example:

```bash
cp .env.example .env
```

Then edit `.env` with your real values.

For MiniMax 2.5 through OpenRouter:

```dotenv
MINIMAX_API_KEY=sk-or-v1-...
MINIMAX_BASE_URL=https://openrouter.ai/api/v1
MEMCI_LLM_MODEL=MiniMax-M2.5
CI_ARTIFACT_ANALYSIS_MODEL=openrouter/minimax/minimax-m2.5
AGENT_MODEL=openrouter/minimax/minimax-m2.5
```

Optional but recommended:

```dotenv
GITHUB_TOKEN=...
```

Notes:

- the benchmark scripts auto-load `.env`
- `sweagent run-batch` also loads `.env`
- `MEMCI_LLM_MODEL=MiniMax-M2.5` is supported by the preprocessing script
- `AGENT_MODEL` should stay provider-qualified for the batch run

## Target Repo Preset

The preset used in this project is:

- `agno-agi/agno`
- `OpenAccess-AI-Collective/axolotl`
- `conan-io/conan`
- `flowersteam/flower`

This preset name is:

- `core_ci_repair`

## Full Run Commands

If your `ci_problem_document` files are already created and the memory bank is not ready yet, you can skip the memory-building steps and run the baseline directly.

### 1. Split benchmark rows

```bash
python scripts/prepare_ci_repair_bench.py \
  --dataset ci-benchmark-user/ci-repair-bench \
  --split train \
  --repo-preset core_ci_repair \
  --memory-ratio 0.3 \
  --min-memory-per-repo 5 \
  --output-dir artifacts/ci_repair_split
```

### 2. Analyze seed rows into CI problem documents

Heuristic mode:

```bash
python scripts/build_ci_problem_documents.py \
  --input-file artifacts/ci_repair_split/memory_seed_rows.jsonl \
  --output-file artifacts/ci_repair_split/memory_seed_rows_analyzed.jsonl \
  --mode heuristic
```

LLM mode with `.env`:

```bash
python scripts/build_ci_problem_documents.py \
  --input-file artifacts/ci_repair_split/memory_seed_rows.jsonl \
  --output-file artifacts/ci_repair_split/memory_seed_rows_analyzed.jsonl \
  --mode llm
```

### 3. Analyze eval rows into CI problem documents

Heuristic mode:

```bash
python scripts/build_ci_problem_documents.py \
  --input-file artifacts/ci_repair_split/eval_rows.jsonl \
  --output-file artifacts/ci_repair_split/eval_rows_analyzed.jsonl \
  --mode heuristic
```

LLM mode with `.env`:

```bash
python scripts/build_ci_problem_documents.py \
  --input-file artifacts/ci_repair_split/eval_rows.jsonl \
  --output-file artifacts/ci_repair_split/eval_rows_analyzed.jsonl \
  --mode llm
```

### 4. Build hierarchical memory bank

```bash
python scripts/build_ci_repair_memory_bank.py \
  --seed-file artifacts/ci_repair_split/memory_seed_rows_analyzed.jsonl \
  --output-file artifacts/ci_repair_split/memory_bank.json
```

This step is only needed for the memory run.

### 5. Build baseline instances

```bash
python scripts/build_ci_repair_instances.py \
  --eval-file artifacts/ci_repair_split/eval_rows_analyzed.jsonl \
  --deployment-type local \
  --output-file artifacts/ci_repair_split/eval_instances_baseline.json
```

This baseline instance build does not load any memory bank. It uses the existing `ci_problem_document` and writes instances with `memory_context=""`.

It also defaults to `local` deployment, so Docker is not required unless you explicitly switch back to `--deployment-type docker`.

### 6. Build memory instances

```bash
python scripts/build_ci_repair_instances.py \
  --eval-file artifacts/ci_repair_split/eval_rows_analyzed.jsonl \
  --deployment-type local \
  --memory-bank artifacts/ci_repair_split/memory_bank.json \
  --output-file artifacts/ci_repair_split/eval_instances_memory.json \
  --retrieval-report artifacts/ci_repair_split/retrieval_report.json \
  --top-k 3 \
  --min-score 0.15
```

### 7. Run baseline with MiniMax 2.5

```bash
sweagent run-batch \
  --config config/benchmarks/ci_repair_memory.yaml \
  --agent.model.name "$AGENT_MODEL" \
  --agent.model.api_base "$MINIMAX_BASE_URL" \
  --agent.model.api_key "$MINIMAX_API_KEY" \
  --instances.type expert_file \
  --instances.path artifacts/ci_repair_split/eval_instances_baseline.json
```

### 8. Run memory version with MiniMax 2.5

```bash
sweagent run-batch \
  --config config/benchmarks/ci_repair_memory.yaml \
  --agent.model.name "$AGENT_MODEL" \
  --agent.model.api_base "$MINIMAX_BASE_URL" \
  --agent.model.api_key "$MINIMAX_API_KEY" \
  --instances.type expert_file \
  --instances.path artifacts/ci_repair_split/eval_instances_memory.json
```

## Baseline First, Memory Later

If the memory bank is not prepared properly yet, use this order:

### Baseline without memory

Required inputs:

- `artifacts/ci_repair_split/eval_rows_analyzed.jsonl`
- each row must already contain `ci_problem_document`

Build the baseline instances:

```bash
python scripts/build_ci_repair_instances.py \
  --eval-file artifacts/ci_repair_split/eval_rows_analyzed.jsonl \
  --deployment-type local \
  --output-file artifacts/ci_repair_split/eval_instances_baseline.json
```

Run the baseline:

```bash
sweagent run-batch \
  --config config/benchmarks/ci_repair_memory.yaml \
  --agent.model.name "$AGENT_MODEL" \
  --agent.model.api_base "$MINIMAX_BASE_URL" \
  --agent.model.api_key "$MINIMAX_API_KEY" \
  --instances.type expert_file \
  --instances.path artifacts/ci_repair_split/eval_instances_baseline.json
```

What this setup means:

- the config is the same `config/benchmarks/ci_repair_memory.yaml`
- the `problem_statement` comes from the generated `ci_problem_document`
- no memory bank is read
- `memory_context` stays empty for every instance
- the repo is fetched from GitHub at `sha_fail` using local deployment, not Docker
- validation is best-effort rather than a strict Docker-based benchmark replay

### Memory run after the bank is ready

Required extra input:

- `artifacts/ci_repair_split/memory_bank.json`

Build memory-enabled instances:

```bash
python scripts/build_ci_repair_instances.py \
  --eval-file artifacts/ci_repair_split/eval_rows_analyzed.jsonl \
  --deployment-type local \
  --memory-bank artifacts/ci_repair_split/memory_bank.json \
  --output-file artifacts/ci_repair_split/eval_instances_memory.json \
  --retrieval-report artifacts/ci_repair_split/retrieval_report.json \
  --top-k 3 \
  --min-score 0.15
```

Run the memory version:

```bash
sweagent run-batch \
  --config config/benchmarks/ci_repair_memory.yaml \
  --agent.model.name "$AGENT_MODEL" \
  --agent.model.api_base "$MINIMAX_BASE_URL" \
  --agent.model.api_key "$MINIMAX_API_KEY" \
  --instances.type expert_file \
  --instances.path artifacts/ci_repair_split/eval_instances_memory.json
```

What changes between the two runs:

- baseline uses `eval_instances_baseline.json`
- memory uses `eval_instances_memory.json`
- the model, scaffold, and benchmark config should stay the same
- only `memory_context` should differ

If you already generated instance files before this change, rebuild them once so they no longer contain `deployment.type: docker`.

## Recommended First Run

If you want the lowest-risk first run:

1. use `heuristic` mode for document building
2. make sure the baseline and memory instance files are created correctly
3. then switch document building to `--mode llm`

That separates:

- benchmark plumbing problems
- model credential problems

## Evaluation Logic

The clean comparison must keep fixed:

- scaffold
- model
- benchmark rows
- prompt template
- CI problem document generation method
- memory split
- runtime settings

Only memory should differ.

Suggested metrics:

- repair success rate
- patch submission count
- retrieval hit rate
- retrieval precision
- per-repo delta: baseline vs memory

## Leakage Rules

To keep the benchmark valid:

- do not put eval rows into the memory bank before evaluation
- do not expose the eval row gold `diff` to the current prompt
- build memory only from the seed split
- use the same eval rows for baseline and memory runs

## Relation To A Larger Custom CI Repair Project

This repo is intentionally not a full copy of a larger custom CI-repair system.

It does not aim to benchmark:

- custom orchestration
- custom fault localization agents
- custom retry loops
- full multi-agent repair logic

It only keeps the parts needed to test the memory hypothesis cleanly:

- CI artifact analysis
- memory construction
- memory retrieval
- prompt injection

## Upstream

This workspace builds on top of:

- https://github.com/SWE-agent/SWE-agent

If you publish results, cite both:

- the upstream `SWE-agent` work
- your own memory-augmented CI benchmark study

## License

This repository inherits the upstream MIT license. See [LICENSE](LICENSE).
