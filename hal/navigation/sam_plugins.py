"""SAM worker plugin resolution for target navigation."""

from __future__ import annotations

from dataclasses import dataclass
import importlib
import importlib.util
import os
from pathlib import Path
import sys
from types import ModuleType
from typing import Any


@dataclass(frozen=True)
class SAMWorkerSpec:
    """Resolved worker command and environment for a SAM-style detector."""

    name: str
    command: tuple[str, ...]
    env: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "command": list(self.command),
            "env": dict(self.env),
        }


def _navigation_sdk_root() -> Path:
    return Path(__file__).resolve().parents[3] / "navigation_sdk"


def _builtin_sam3_worker_spec(config: dict[str, Any] | None = None) -> SAMWorkerSpec:
    config = dict(config or {})
    worker_path = Path(
        str(
            config.get("worker_path")
            or os.environ.get("OEA_TARGET_NAV_SAM_WORKER", "")
            or os.environ.get("NAV_SAM3_WORKER", "")
            or (_navigation_sdk_root() / "navigation_mcp" / "sam3_worker.py")
        )
    ).expanduser().resolve()

    python_executable = str(
        config.get("python")
        or os.environ.get("OEA_TARGET_NAV_SAM_PYTHON", "")
        or os.environ.get("NAV_SAM3_PYTHON", "")
        or sys.executable
    ).strip()

    env = dict(os.environ)
    mapping = {
        "repo_path": "NAV_SAM3_REPO",
        "checkpoint_path": "NAV_SAM3_CKPT",
        "device": "NAV_SAM3_DEVICE",
    }
    for config_key, env_key in mapping.items():
        value = config.get(config_key) or os.environ.get(env_key, "")
        if value:
            env[env_key] = str(value)
    return SAMWorkerSpec(
        name=str(config.get("name") or "sam3_builtin"),
        command=(python_executable, str(worker_path)),
        env=env,
    )


def _load_module_from_path(module_path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"oea_sam_plugin_{module_path.stem}", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load SAM plugin module from {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _module_to_spec(module: ModuleType, plugin_name: str, config: dict[str, Any] | None) -> SAMWorkerSpec:
    if hasattr(module, "get_sam_worker_spec"):
        value = module.get_sam_worker_spec(config or {})
    elif hasattr(module, "SAM_WORKER_SPEC"):
        value = getattr(module, "SAM_WORKER_SPEC")
    else:
        raise RuntimeError(f"SAM plugin '{plugin_name}' does not expose get_sam_worker_spec or SAM_WORKER_SPEC")

    if isinstance(value, SAMWorkerSpec):
        return value
    if not isinstance(value, dict):
        raise RuntimeError(f"SAM plugin '{plugin_name}' returned unsupported spec type: {type(value)!r}")
    command = value.get("command") or []
    if not isinstance(command, (list, tuple)) or not command:
        raise RuntimeError(f"SAM plugin '{plugin_name}' must provide a non-empty command")
    env = value.get("env") or {}
    if not isinstance(env, dict):
        raise RuntimeError(f"SAM plugin '{plugin_name}' env must be a dict")
    return SAMWorkerSpec(
        name=str(value.get("name") or plugin_name),
        command=tuple(str(item) for item in command),
        env={str(key): str(val) for key, val in env.items()},
    )


def resolve_sam_worker_spec(plugin_name: str | None = None, config: dict[str, Any] | None = None) -> SAMWorkerSpec:
    """Resolve a SAM worker plugin from builtins, module path, or dotted module."""

    resolved_name = (plugin_name or os.environ.get("OEA_TARGET_NAV_SAM_PLUGIN") or "sam3_builtin").strip()
    if resolved_name in {"sam3", "sam3_builtin", "builtin"}:
        return _builtin_sam3_worker_spec(config)

    if resolved_name.endswith(".py") or "/" in resolved_name:
        module = _load_module_from_path(Path(resolved_name).expanduser().resolve())
        return _module_to_spec(module, resolved_name, config)

    module = importlib.import_module(resolved_name)
    return _module_to_spec(module, resolved_name, config)
