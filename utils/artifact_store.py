"""
artifact_store.py — safe artifact I/O for the pipeline.

Two responsibilities:
  1. Atomic writes  — data is written to a .tmp file then os.replace()'d into
                      place. A crash mid-write leaves the old artifact intact
                      rather than a partial/corrupt file.
  2. Typed reads    — load JSON artifacts with a size sanity check; return None
                      instead of raising so callers can decide whether to skip
                      or re-run the stage.

Usage in stages:
    from utils.artifact_store import atomic_write_json, read_json_artifact

    # In _write():
    atomic_write_json(OUTPUT_PATH, data)

    # In resume logic (run_pipeline.py):
    data = read_json_artifact(ENTITIES)   # None if missing or empty
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from typing import Any

logger = logging.getLogger(__name__)

# Warn if an artifact is larger than this (indicates runaway LLM output)
_WARN_SIZE_BYTES = 50 * 1024 * 1024   # 50 MB


def atomic_write_json(path: str, data: Any, indent: int = 2) -> None:
    """
    Write JSON to `path` atomically using a sibling temp file + os.replace().

    Guarantees:
    - Readers never see a partially written file.
    - If the process crashes during the write, the old artifact (if any) is
      preserved and the stale .tmp file is left behind (safe to delete on retry).
    """
    dir_ = os.path.dirname(os.path.abspath(path))
    os.makedirs(dir_, exist_ok=True)

    # Write to a sibling temp file in the same directory so os.replace() is
    # atomic (rename on POSIX; best-effort on Windows — same volume required).
    fd, tmp_path = tempfile.mkstemp(dir=dir_, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=indent)
        os.replace(tmp_path, path)
        logger.debug(f"[ARTIFACT] Written atomically -> {path}")
    except Exception:
        # Clean up the temp file so it doesn't clutter the directory.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def read_json_artifact(path: str) -> list | dict | None:
    """
    Load a JSON artifact from disk.

    Returns None (instead of raising) when:
    - The file does not exist
    - The file is empty
    - The file contains invalid JSON (logged as WARNING)

    This lets resume logic fall back to re-running the stage rather than
    crashing on a corrupt/partial artifact.
    """
    if not os.path.exists(path):
        return None

    size = os.path.getsize(path)
    if size == 0:
        logger.warning(f"[ARTIFACT] Empty file, skipping: {path}")
        return None

    if size > _WARN_SIZE_BYTES:
        logger.warning(f"[ARTIFACT] Unusually large artifact ({size // 1024} KB): {path}")

    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        logger.warning(f"[ARTIFACT] Corrupt JSON in {path}: {e} — will re-generate")
        return None


def artifact_is_valid(path: str) -> bool:
    """True if the artifact exists, is non-empty, and is parseable JSON."""
    return read_json_artifact(path) is not None
