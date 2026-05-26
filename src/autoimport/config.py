"""Shared config loading for the CLI and the pylsp plugin."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import xdg_base_dirs
from maison import UserConfig


def load_config(
    workspace_root: str | None = None,
    extra_config_file: str | None = None,
) -> dict[str, Any]:
    """Merge the global XDG config, the project ``pyproject.toml`` and an optional file.

    When ``workspace_root`` is ``None`` (CLI invocation) a cwd-relative
    ``pyproject.toml`` is appended unconditionally — maison ignores missing
    paths. When ``workspace_root`` is set (LSP invocation) the workspace's
    ``pyproject.toml`` is appended only if it actually exists, so we don't
    accidentally resolve against the editor's cwd.
    """
    sources: list[str] = []

    global_path = xdg_base_dirs.xdg_config_home() / "autoimport" / "config.toml"
    if global_path.is_file():
        sources.append(str(global_path))

    if workspace_root is None:
        sources.append("pyproject.toml")
    else:
        pyproject = Path(workspace_root) / "pyproject.toml"
        if pyproject.is_file():
            sources.append(str(pyproject))

    if extra_config_file is not None:
        sources.append(extra_config_file)

    if not sources:
        return {}

    return UserConfig(
        package_name="autoimport", source_files=sources, merge_configs=True
    ).values
