from __future__ import annotations

import importlib
import importlib.util
import tomllib
import tomli_w
from pathlib import Path

from ffast.config.models import ProjectConfig


def load_project_config(path: Path) -> ProjectConfig:
    with open(path, "rb") as f:
        data = tomllib.load(f)
    return ProjectConfig.model_validate(data)


def load_metric_modules(config: ProjectConfig, config_path: Path) -> None:
    base = config_path.parent
    for m in config.metrics.modules:
        if not m.enabled:
            continue
        if m.import_path is not None:
            importlib.import_module(m.import_path)
            continue
        resolved = (base / m.path).resolve()
        spec = importlib.util.spec_from_file_location(Path(m.path).stem, resolved)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)


def _prune_empty(d: dict) -> dict:
    result = {}
    for k, v in d.items():
        if isinstance(v, dict):
            v = _prune_empty(v)
            if v:
                result[k] = v
        elif isinstance(v, list):
            if v:
                result[k] = v
        else:
            result[k] = v
    return result


def save_project_config(config: ProjectConfig, path: Path) -> None:
    """Write *config* to *path* as TOML, omitting None values and empty sections."""
    data = _prune_empty(config.model_dump(exclude_none=True))
    with open(path, "wb") as f:
        tomli_w.dump(data, f)


def discover_config(start: Path) -> Path | None:
    current = start if start.is_dir() else start.parent
    while True:
        candidate = current / "ffast.toml"
        if candidate.exists():
            return candidate
        parent = current.parent
        if parent == current:
            return None
        current = parent
