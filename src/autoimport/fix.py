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
    insert_imports_in_text,
    restore_compound_fmt_skip,
    stash_compound_fmt_skip,
)
from autoimport.finder import PackageFinder

_RUFF_TIMEOUT_SECONDS = 10

log = logging.getLogger(__name__)

# Matches lines like `import x; y()` or `from a.b import c; d()`. Lines marked
# `# fmt: skip` have already been replaced with `pass` placeholders by
# stash_compound_fmt_skip, so they won't match here.
_COMPOUND_IMPORT_RE = re.compile(
    r"^\s*(?:import\s+\w+|from\s+[\w.]+\s+import\s+\w+)\s*;",
    re.MULTILINE,
)


def _expand_paths(paths: Sequence[Path]) -> list[Path]:
    result = []
    for path in paths:
        if path.is_dir():
            result.extend(sorted(path.rglob("*.py")))
        else:
            result.append(path)
    return result


def _has_compound_import(path: Path) -> bool:
    try:
        return bool(_COMPOUND_IMPORT_RE.search(path.read_text()))
    except OSError:
        return False


def fix_files(
    files: Sequence[Path],
    config: dict[str, Any] | None = None,
) -> None:
    """Fix imports in ``files``."""
    fnames = list(map(str, files))
    expanded = _expand_paths(files)

    stashed = stash_compound_fmt_skip(expanded)
    try:
        # ruff format splits compound `import X; Y()` statements onto separate
        # lines. The E402 path below deletes whole lines by number, so the
        # import has to be on its own line first — but only files that actually
        # contain a compound import need this pre-pass.
        preformat = [str(p) for p in expanded if _has_compound_import(p)]
        if preformat:
            subprocess.run(["ruff", "format", "--silent", *preformat], check=False)

        result = subprocess.run(
            [
                "ruff",
                "check",
                "--select",
                "E402,F821,F822,F401,I001",
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
        files_needing_ruff_fix: set[Path] = set()
        for msg in messages:
            fname = Path(msg["filename"])
            code = msg["code"]
            if code in ("F401", "I001"):
                files_needing_ruff_fix.add(fname)
                continue
            if msg["fix"] is not None:
                continue
            if code in ("F821", "F822"):
                match = re.search(r"`([^`]+)`", msg["message"])
                if not match:
                    continue

                name = match.group(1)
                packages_missing.add(name)
                files_missing[fname].add(name)
            elif code == "E402":
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

        modified: set[Path] = set(lines_to_delete)
        for fname, add_imports in imports_to_add.items():
            if add_imports:
                insert_imports(fname, add_imports)
                modified.add(fname)

        files_to_fix = modified | files_needing_ruff_fix
        if files_to_fix:
            fix_args = [str(p) for p in sorted(files_to_fix)]
            subprocess.run(
                [
                    "ruff",
                    "check",
                    "--exit-zero",
                    "--silent",
                    "--select",
                    "I001,F401",
                    "--fix",
                    *fix_args,
                ],
                check=False,
            )
            subprocess.run(["ruff", "format", "--silent", *fix_args], check=False)
    finally:
        restore_compound_fmt_skip(expanded, stashed)


def insert_chosen_import(file: Path, import_statement: str) -> None:
    """Insert a specific import line into ``file`` and let ruff isort sort it.

    This skips the finder entirely — used by the pylsp plugin when the user
    has already picked one of the candidates surfaced by
    :meth:`PackageFinder.find_candidates`.
    """
    stashed = stash_compound_fmt_skip([file])
    try:
        insert_imports(file, [import_statement])
        subprocess.run(
            [
                "ruff",
                "check",
                "--exit-zero",
                "--silent",
                "--select",
                "I001",
                "--fix",
                str(file),
            ],
            check=False,
            timeout=_RUFF_TIMEOUT_SECONDS,
        )
    finally:
        restore_compound_fmt_skip([file], stashed)


def insert_chosen_import_text(source: str, import_statement: str) -> str:
    """In-memory variant of :func:`insert_chosen_import`.

    The import is inserted into ``source`` in memory and the result is piped
    through ``ruff check --select I001 --fix`` over stdin/stdout, so neither
    autoimport nor ruff need to touch the filesystem. Used by the pylsp plugin
    to keep the LSP request thread off the disk hot path.
    """
    new_source = insert_imports_in_text(source, [import_statement])
    try:
        result = subprocess.run(
            [
                "ruff",
                "check",
                "--exit-zero",
                "--silent",
                "--select",
                "I001",
                "--fix",
                "--stdin-filename",
                "buffer.py",
                "-",
            ],
            input=new_source,
            capture_output=True,
            text=True,
            timeout=_RUFF_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        log.warning("ruff timed out while sorting imports; returning unsorted output")
        return new_source

    return result.stdout if result.stdout else new_source
