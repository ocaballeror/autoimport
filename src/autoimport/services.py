"""Define all the orchestration functionality required by the program to work."""

import json
import re
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Any

from autoimport.model import PackageFinder


def _find_header_end(lines: list[str]) -> int:
    """Return the line index after any shebang, leading comments, and module docstring."""
    i = 0
    n = len(lines)

    while i < n and (lines[i].startswith("#") or not lines[i].strip()):
        i += 1

    if i < n:
        stripped = lines[i].strip()
        if stripped.startswith('"""') or stripped.startswith("'''"):
            quote = stripped[:3]
            rest = stripped[3:]
            if rest.endswith(quote) and len(rest) >= 3:
                i += 1
            else:
                i += 1
                while i < n and quote not in lines[i]:
                    i += 1
                i += 1

    return i


def insert_imports(file: Path, imports: list[str]) -> str:
    """Insert import statements after the file header (shebang, comments, docstring)."""
    source = file.read_text()
    lines = source.splitlines(keepends=True)
    i = _find_header_end(lines)

    import_block = "\n".join(imports) + "\n"
    before = "".join(lines[:i])
    rest = "".join(lines[i:])

    if before and not before.endswith("\n\n"):
        import_block = "\n" + import_block

    source = before + import_block + rest
    file.write_text(source)


def fix_files(files: list[Path], config: dict[str, Any] | None = None) -> None:
    fnames = list(map(str, files))
    result = subprocess.run(
        [
            "ruff",
            "check",
            "--select",
            "F821,F822",
            "--output-format",
            "json",
            *fnames,
        ],
        capture_output=True,
        text=True,
    )
    messages = json.loads(result.stdout) if result.stdout else []

    packages_missing: set[str] = set()
    files_missing: dict[Path, set[str]] = defaultdict(set)
    for msg in messages:
        if msg["code"] not in ("F821", "F822"):
            continue
        match = re.search(r"`([^`]+)`", msg["message"])
        if not match:
            continue

        fname = Path(msg["filename"])
        name = match.group(1)

        packages_missing.add(name)
        files_missing[fname].add(name)

    finder = PackageFinder(config)
    finder.index_packages(packages_missing)

    for fname, names in files_missing.items():
        imports_to_add = []
        for pkg in names:
            import_stmt = finder.find_package(pkg, fname)
            if import_stmt:
                imports_to_add.append(import_stmt)

        insert_imports(fname, imports_to_add)

    subprocess.check_call(["ruff", "format", "--silent", *fnames])
    subprocess.check_call(
        ["ruff", "check", "--exit-zero", "--silent", "--select", "I001,F401,E402", "--fix", *fnames]
    )
    subprocess.check_call(["ruff", "format", "--silent", *fnames])
