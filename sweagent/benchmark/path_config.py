"""
Dynamic Path Configuration Utilities

This module provides utilities for dynamic path resolution, supporting:
- Environment variables (BENCHMARK_WORK_DIR, SWE_AGENT_CONFIG_ROOT, etc.)
- Relative paths (./artifacts, ../config)
- Absolute paths (/path/to/artifacts)
- Tilde expansion (~/.config)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from sweagent.utils.log import get_logger

logger = get_logger("path-config", emoji="📁")


def get_benchmark_work_dir(default: Optional[str] = None) -> Path:
    """Get benchmark working directory from env var or default.
    
    Environment variable: BENCHMARK_WORK_DIR
    Default: {REPO_ROOT}/artifacts
    """
    if path_str := os.getenv("BENCHMARK_WORK_DIR"):
        path = Path(path_str).expanduser().resolve()
        logger.info(f"Using BENCHMARK_WORK_DIR: {path}")
        return path
    
    if default:
        path = Path(default).expanduser().resolve()
        logger.info(f"Using default BENCHMARK_WORK_DIR: {path}")
        return path
    
    from sweagent import REPO_ROOT
    path = REPO_ROOT / "artifacts"
    logger.info(f"Using repo artifacts directory: {path}")
    return path


def get_output_dir(subdir: str = "ci_repair_split") -> Path:
    """Get output directory for benchmark results.
    
    Environment variable: BENCHMARK_OUTPUT_DIR
    Default: {BENCHMARK_WORK_DIR}/{subdir}
    """
    if path_str := os.getenv("BENCHMARK_OUTPUT_DIR"):
        path = Path(path_str).expanduser().resolve()
        logger.info(f"Using BENCHMARK_OUTPUT_DIR: {path}")
        return path
    
    work_dir = get_benchmark_work_dir()
    path = work_dir / subdir
    logger.info(f"Using output directory: {path}")
    return path


def get_memory_bank_dir() -> Path:
    """Get memory bank directory.
    
    Environment variable: MEMORY_BANK_DIR
    Default: {BENCHMARK_OUTPUT_DIR}
    """
    if path_str := os.getenv("MEMORY_BANK_DIR"):
        path = Path(path_str).expanduser().resolve()
        logger.info(f"Using MEMORY_BANK_DIR: {path}")
        return path
    
    path = get_output_dir()
    logger.info(f"Using memory bank directory: {path}")
    return path


def get_config_root() -> Path:
    """Get configuration root directory for relative path resolution.
    
    Environment variable: SWE_AGENT_CONFIG_ROOT
    Default: REPO_ROOT
    """
    if path_str := os.getenv("SWE_AGENT_CONFIG_ROOT"):
        path = Path(path_str).expanduser().resolve()
        logger.info(f"Using SWE_AGENT_CONFIG_ROOT: {path}")
        return path
    
    from sweagent import REPO_ROOT
    logger.info(f"Using repo root as config root: {REPO_ROOT}")
    return REPO_ROOT


def get_temp_dir() -> Path:
    """Get temporary directory for scratch files.
    
    Environment variable: TEMP_DIR
    Default: system temp directory
    """
    if path_str := os.getenv("TEMP_DIR"):
        if path_str.strip():
            path = Path(path_str).expanduser().resolve()
            logger.info(f"Using TEMP_DIR: {path}")
            path.mkdir(parents=True, exist_ok=True)
            return path
    
    import tempfile
    path = Path(tempfile.gettempdir())
    logger.info(f"Using system temp directory: {path}")
    return path


def resolve_path(path_input: str | Path, base: Optional[Path] = None) -> Path:
    """Resolve a path with support for env vars and relative paths.
    
    Args:
        path_input: Path string or Path object
        base: Base directory for relative paths (default: config root)
    
    Returns:
        Absolute Path object
    """
    if not path_input:
        raise ValueError("Path input cannot be empty")
    
    # Expand environment variables
    if isinstance(path_input, str):
        path_str = os.path.expandvars(path_input)
        path = Path(path_str)
    else:
        path = path_input
    
    # Expand user home
    path = path.expanduser()
    
    # Handle relative paths
    if not path.is_absolute():
        base = base or get_config_root()
        path = base / path
    
    # Resolve symlinks and normalize
    path = path.resolve()
    
    logger.debug(f"Resolved path {path_input} → {path}")
    return path


def ensure_dir(path: str | Path) -> Path:
    """Resolve a path and ensure the directory exists.
    
    Args:
        path: Path string or Path object
    
    Returns:
        Absolute Path object
    """
    resolved = resolve_path(path)
    resolved.mkdir(parents=True, exist_ok=True)
    logger.debug(f"Ensured directory exists: {resolved}")
    return resolved


# Configuration constants (for reference in scripts)
CONFIG_KEYS = {
    # Paths
    "BENCHMARK_WORK_DIR": "Base directory for all benchmark artifacts",
    "BENCHMARK_OUTPUT_DIR": "Directory for benchmark results",
    "MEMORY_BANK_DIR": "Directory for memory bank files",
    "SWE_AGENT_CONFIG_ROOT": "Root for config-relative paths",
    "TEMP_DIR": "Temporary directory for scratch files",
    
    # Dataset
    "CI_BENCHMARK_DATASET": "HuggingFace dataset identifier",
    "CI_BENCHMARK_SPLIT": "Dataset split (train/validation/test)",
    "CI_REPAIR_REPO_PRESET": "Repository preset for filtering",
    
    # Memory
    "CI_MEMORY_SEED_RATIO": "Seed ratio for memory construction",
    "CI_MIN_MEMORY_PER_REPO": "Minimum seed rows per repository",
    "MEMORY_MODE": "Memory mode (baseline/memory)",
    "MEMORY_TOP_K": "Top-K candidates for L1 retrieval",
    "MEMORY_ABLATION_LEVELS": "Ablation levels (L1/L1+L2/L1+L2+L3)",
    
    # Analysis
    "CI_ANALYSIS_MODE": "Analysis mode (heuristic/llm)",
    "CI_MAX_LOG_CHARS": "Maximum log characters for analysis",
    
    # Deployment
    "DEPLOYMENT_TYPE": "Deployment type (local/docker)",
    "DEPLOYMENT_IMAGE": "Docker image for deployment",
    
    # System
    "CI_MODE": "CI/CD mode (disable interactive)",
    "LOG_LEVEL": "Logging level (DEBUG/INFO/WARNING/ERROR)",
    "DEBUG_MEMORY_RETRIEVAL": "Enable debug logging for retrieval",
    "KEEP_TEMP_FILES": "Keep temporary files for inspection",
}


def print_config_summary() -> None:
    """Print summary of current configuration."""
    logger.info("=== Configuration Summary ===")
    
    # Print loaded paths
    paths = {
        "BENCHMARK_WORK_DIR": get_benchmark_work_dir(),
        "BENCHMARK_OUTPUT_DIR": get_output_dir(),
        "MEMORY_BANK_DIR": get_memory_bank_dir(),
        "SWE_AGENT_CONFIG_ROOT": get_config_root(),
        "TEMP_DIR": get_temp_dir(),
    }
    
    for name, path in paths.items():
        logger.info(f"  {name}: {path}")
    
    # Print relevant env vars
    logger.info("Environment Configuration:")
    for key in CONFIG_KEYS:
        if value := os.getenv(key):
            logger.info(f"  {key}: {value}")
