"""Orchestration: run ruff, resolve missing imports, and rewrite files."""

import json
import logging
import re
import subprocess
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from autoimport.files import (
    delete_lines,
    insert_imports,
    restore_compound_fmt_skip,
    stash_compound_fmt_skip,
)
from autoimport.finder import PackageFinder

log = logging.getLogger(__name__)


def _expand_paths(paths: Sequence[Path]) -> list[Path]:
    result = []
    for path in paths:
        if path.is_dir():
            result.extend(sorted(path.rglob("*.py")))
        else:
            result.append(path)
    return result


def fix_files(
    files: Sequence[Path],
    config: dict[str, Any] | None = None,
    only_names: set[str] | None = None,
) -> None:
    """Fix imports in ``files``.

    When ``only_names`` is provided, the function operates in targeted mode:
    it skips ruff's scan, the E402 reshuffling and the trailing F401/format
    passes, and only inserts imports for the supplied names. The rest of the
    file is left untouched apart from an I001 sort that places the new import
    in the right block.
    """
    fnames = list(map(str, files))
    expanded = _expand_paths(files)

    stashed = stash_compound_fmt_skip(expanded)
    try:
        if only_names is not None:
            _fix_specific_names(expanded, config, only_names)
            return

        subprocess.run(["ruff", "format", "--silent", *fnames], check=False)
        result = subprocess.run(
            [
                "ruff",
                "check",
                "--select",
                "E402,F821,F822",
                "--output-format",
                "json",
                *fnames,
            ],
            capture_output=True,
            text=True,
        )
        if result.stdout:
            messages = json.loads(result.stdout)
        else:
            if result.returncode not in (0, 1):
                log.debug(
                    "ruff check exited %d with no stdout: %s", result.returncode, result.stderr
                )
            messages = []

        packages_missing: set[str] = set()
        files_missing: dict[Path, set[str]] = defaultdict(set)
        lines_to_delete: dict[Path, set[int]] = defaultdict(set)
        for msg in messages:
            if msg["fix"] is not None:
                continue

            fname = Path(msg["filename"])
            if msg["code"] in ("F821", "F822"):
                match = re.search(r"`([^`]+)`", msg["message"])
                if not match:
                    continue

                name = match.group(1)
                packages_missing.add(name)
                files_missing[fname].add(name)
            elif msg["code"] == "E402":
                for lineno in range(msg["location"]["row"], msg["end_location"]["row"] + 1):
                    lines_to_delete[fname].add(lineno)

        imports_to_add: dict[Path, list[str]] = defaultdict(list)
        imports_to_add.update(
            {fname: delete_lines(fname, lines) for fname, lines in lines_to_delete.items()}
        )

        finder = PackageFinder(config)
        finder.index_packages(packages_missing)

        for fname, names in files_missing.items():
            for pkg in names:
                import_stmt = finder.find_package(pkg, fname)
                if import_stmt:
                    imports_to_add[fname].append(import_stmt)

        for fname, add_imports in imports_to_add.items():
            insert_imports(fname, add_imports)

        subprocess.run(
            ["ruff", "check", "--exit-zero", "--silent", "--select", "I001,F401", "--fix", *fnames],
            check=False,
        )
        subprocess.run(["ruff", "format", "--silent", *fnames], check=False)
    finally:
        restore_compound_fmt_skip(expanded, stashed)


def _fix_specific_names(
    expanded: list[Path],
    config: dict[str, Any] | None,
    only_names: set[str],
) -> None:
    finder = PackageFinder(config)
    finder.index_packages(only_names)

    touched: list[str] = []
    for fname in expanded:
        resolved: list[str] = []
        for name in only_names:
            import_stmt = finder.find_package(name, fname)
            if import_stmt:
                resolved.append(import_stmt)
        if resolved:
            insert_imports(fname, resolved)
            touched.append(str(fname))

    if touched:
        subprocess.run(
            ["ruff", "check", "--exit-zero", "--silent", "--select", "I001", "--fix", *touched],
            check=False,
        )
