"""pylsp plugin that exposes autoimport as a quickfix code action."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

import xdg_base_dirs
from maison import UserConfig
from pylsp import hookimpl

from autoimport.finder import PackageFinder
from autoimport.fix import insert_chosen_import

logger = logging.getLogger(__name__)

COMMAND_FIX_IMPORTS = "autoimport.fixImports"

# Diagnostic identifiers that signal a missing import.
_MISSING_IMPORT_CODES = {"F821", "F822", "E0602"}
_UNDEFINED_NAME_RE = re.compile(r"undefined name|undefined variable", re.IGNORECASE)
# Matches the offending name when wrapped in backticks (ruff) or single quotes (pyflakes/pylint).
_NAME_RE = re.compile(r"`([^`]+)`|'([^']+)'")


def _is_missing_import_diagnostic(diagnostic: dict[str, Any]) -> bool:
    code = diagnostic.get("code")
    if isinstance(code, str) and code in _MISSING_IMPORT_CODES:
        return True
    message = diagnostic.get("message") or ""
    return bool(_UNDEFINED_NAME_RE.search(message))


def _extract_name(diagnostic: dict[str, Any]) -> str | None:
    message = diagnostic.get("message") or ""
    match = _NAME_RE.search(message)
    if not match:
        return None
    return match.group(1) or match.group(2)


def _load_config(workspace_root: str | None) -> dict[str, Any]:
    config_files: list[str] = []

    global_config_path = xdg_base_dirs.xdg_config_home() / "autoimport" / "config.toml"
    if global_config_path.is_file():
        config_files.append(str(global_config_path))

    if workspace_root:
        project_config = Path(workspace_root) / "pyproject.toml"
        if project_config.is_file():
            config_files.append(str(project_config))

    if not config_files:
        return {}

    return UserConfig(
        package_name="autoimport", source_files=config_files, merge_configs=True
    ).values


def _make_finder(workspace_root: str | None) -> PackageFinder:
    return PackageFinder(_load_config(workspace_root))


@hookimpl
def pylsp_settings() -> dict[str, Any]:
    logger.info("Initializing autoimport pylsp plugin")
    return {"plugins": {"autoimport": {"enabled": True}}}


@hookimpl
def pylsp_code_actions(
    config: Any,
    workspace: Any,
    document: Any,
    range: dict[str, Any],
    context: dict[str, Any],
) -> list[dict[str, Any]]:
    diagnostics = context.get("diagnostics") or []

    seen: dict[str, dict[str, Any]] = {}
    for diagnostic in diagnostics:
        if not _is_missing_import_diagnostic(diagnostic):
            continue
        name = _extract_name(diagnostic)
        if not name or name in seen:
            continue
        seen[name] = diagnostic

    if not seen:
        return []

    finder = _make_finder(workspace.root_path)
    finder.index_packages(seen.keys())

    file_path = Path(document.path) if document.path else None

    actions: list[dict[str, Any]] = []
    for name, diagnostic in seen.items():
        candidates = finder.find_candidates(name, file_path) if file_path else []
        if not candidates:
            continue
        for candidate in candidates:
            title = (
                f"autoimport: `{candidate}`"
                if len(candidates) > 1
                else f"autoimport: add `{candidate}`"
            )
            actions.append(
                {
                    "title": title,
                    "kind": "quickfix",
                    "diagnostics": [diagnostic],
                    "command": {
                        "title": title,
                        "command": COMMAND_FIX_IMPORTS,
                        "arguments": [document.uri, candidate],
                    },
                }
            )
    return actions


@hookimpl
def pylsp_commands(config: Any, workspace: Any) -> list[str]:
    return [COMMAND_FIX_IMPORTS]


@hookimpl
def pylsp_execute_command(
    config: Any,
    workspace: Any,
    command: str,
    arguments: list[Any],
) -> None:
    if command != COMMAND_FIX_IMPORTS:
        return None

    if len(arguments) < 2:
        logger.warning("autoimport: %s requires (uri, import_statement)", command)
        return None

    document_uri, import_statement = arguments[0], arguments[1]

    document = workspace.get_document(document_uri)
    source = document.source
    target_dir = Path(document.path).parent if document.path else Path(workspace.root_path)

    # Write source to a sibling temp file so pyprojroot/finder resolve the right project.
    tmp = NamedTemporaryFile("w", suffix=".py", dir=str(target_dir), delete=False, encoding="utf-8")
    tmp_path = Path(tmp.name)
    try:
        tmp.write(source)
        tmp.close()

        insert_chosen_import(tmp_path, import_statement)
        new_text = tmp_path.read_text(encoding="utf-8")
    finally:
        tmp_path.unlink(missing_ok=True)

    if new_text == source:
        return None

    lines = source.split("\n")
    end_line = len(lines) - 1
    end_char = len(lines[-1])

    workspace_edit = {
        "changes": {
            document_uri: [
                {
                    "range": {
                        "start": {"line": 0, "character": 0},
                        "end": {"line": end_line, "character": end_char},
                    },
                    "newText": new_text,
                }
            ]
        }
    }

    workspace.apply_edit(workspace_edit)
    return None
