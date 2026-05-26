"""pylsp plugin that exposes autoimport as a quickfix code action."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from pylsp import hookimpl

from autoimport.config import load_config
from autoimport.finder import PackageFinder
from autoimport.fix import insert_chosen_import

logger = logging.getLogger(__name__)

COMMAND_FIX_IMPORTS = "autoimport.fixImports"

# Diagnostic identifiers that signal a missing import. F822 (undefined name in
# __all__) is intentionally excluded — adding an import does not satisfy it.
_MISSING_IMPORT_CODES = {"F821", "E0602"}
_UNDEFINED_NAME_RE = re.compile(r"undefined name|undefined variable", re.IGNORECASE)
# Matches the offending name in any of the quoting styles emitted by linters:
# backticks (ruff), straight single/double quotes (pyflakes/pylint/pyright) and
# Unicode curly quotes (some pyright/pylance builds).
_NAME_RE = re.compile(
    r"`([^`]+)`"
    r"|'([^']+)'"
    r'|"([^"]+)"'
    r"|‘([^’]+)’"
    r"|“([^”]+)”"
)

_finder_cache: dict[str, PackageFinder] = {}
_config_cache: dict[str, dict[str, Any]] = {}


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
    return next((group for group in match.groups() if group), None)


def _cache_key(workspace_root: str | None) -> str:
    return workspace_root or ""


def _get_config(workspace_root: str | None) -> dict[str, Any]:
    key = _cache_key(workspace_root)
    if key not in _config_cache:
        _config_cache[key] = load_config(workspace_root)
    return _config_cache[key]


def _get_finder(workspace_root: str | None) -> PackageFinder:
    key = _cache_key(workspace_root)
    if key not in _finder_cache:
        _finder_cache[key] = PackageFinder(_get_config(workspace_root))
    return _finder_cache[key]


def _write_buffer_to_temp(source: str) -> Path:
    """Persist the in-memory buffer to a temp file outside the workspace.

    The finder and ruff need a real path to read from. Using the system tempdir
    keeps file-watchers and project tooling from seeing the scratch file.
    """
    tmp = NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8")
    try:
        tmp.write(source)
    finally:
        tmp.close()
    return Path(tmp.name)


def _minimal_text_edit(old: str, new: str) -> dict[str, Any]:
    """Build an LSP TextEdit covering only the lines that actually changed.

    Avoids replacing the entire document, which would collapse the user's undo
    history into a single step and reset cursor / selection position.
    """
    old_lines = old.splitlines(keepends=True)
    new_lines = new.splitlines(keepends=True)

    prefix = 0
    max_common = min(len(old_lines), len(new_lines))
    while prefix < max_common and old_lines[prefix] == new_lines[prefix]:
        prefix += 1

    suffix = 0
    while (
        suffix < len(old_lines) - prefix
        and suffix < len(new_lines) - prefix
        and old_lines[-1 - suffix] == new_lines[-1 - suffix]
    ):
        suffix += 1

    start_line = prefix
    end_line = len(old_lines) - suffix
    new_text = "".join(new_lines[prefix : len(new_lines) - suffix])

    return {
        "range": {
            "start": {"line": start_line, "character": 0},
            "end": {"line": end_line, "character": 0},
        },
        "newText": new_text,
    }


@hookimpl
def pylsp_settings(config: Any) -> dict[str, Any]:
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

    finder = _get_finder(workspace.root_path)
    finder.index_packages(seen.keys())

    # Always analyze against a snapshot of the in-memory buffer so unsaved
    # edits are honoured and unsaved buffers still get suggestions.
    buffer_path = _write_buffer_to_temp(document.source)

    try:
        actions: list[dict[str, Any]] = []
        for name, diagnostic in seen.items():
            candidates = finder.find_candidates(name, buffer_path)
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
    finally:
        buffer_path.unlink(missing_ok=True)


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

    tmp_path = _write_buffer_to_temp(source)
    try:
        insert_chosen_import(tmp_path, import_statement)
        new_text = tmp_path.read_text(encoding="utf-8")
    finally:
        tmp_path.unlink(missing_ok=True)

    if new_text == source:
        return None

    workspace_edit = {
        "changes": {document_uri: [_minimal_text_edit(source, new_text)]}
    }

    workspace.apply_edit(workspace_edit)
    return None
