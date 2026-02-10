#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


def resolve_experiment_id(value: Optional[str]) -> str:
    if value:
        return value
    return datetime.now(timezone.utc).strftime("fig3-%Y%m%d-%H%M%S")


def _git_commit(root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()
    except Exception:
        return "unknown"


def build_env(
    *,
    experiment_id: str,
    script: str,
    config: Dict[str, Any],
    dependencies: Dict[str, Any],
    extra: Optional[Dict[str, Any]] = None,
    root_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    if root_dir is None:
        root_dir = Path(__file__).resolve().parents[1]
    env = {
        "experiment_id": experiment_id,
        "script": script,
        "git_commit": _git_commit(root_dir),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "config": config,
        "dependencies": dependencies,
    }
    if extra:
        env.update(extra)
    return env


def write_env(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
