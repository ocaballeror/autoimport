"""Define all the orchestration functionality required by the program to work."""

import json
import re
import shutil
import subprocess
from collections import defaultdict
from pathlib import Path
from tempfile import NamedTemporaryFile
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


def delete_lines(path: Path, line_numbers: list[int]) -> list[str]:
    removed = []
    with NamedTemporaryFile(mode="w", delete=False, encoding="utf-8") as tmp:
        with path.open("r", encoding="utf-8") as src:
            for idx, line in enumerate(src, start=1):
                if idx in line_numbers:
                    removed.append(line)
                else:
                    tmp.write(line)

    shutil.move(tmp.name, path)
    return removed


def fix_files(files: list[Path], config: dict[str, Any] | None = None) -> None:
    fnames = list(map(str, files))
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
    messages = json.loads(result.stdout) if result.stdout else []

    packages_missing: set[str] = set()
    files_missing: dict[Path, set[str]] = defaultdict(set)
    lines_to_delete: dict[Path, list[int]] = defaultdict(list)
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
                lines_to_delete[fname].append(lineno)

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

    subprocess.check_call(["ruff", "format", "--silent", *fnames])
    subprocess.check_call(
        ["ruff", "check", "--exit-zero", "--silent", "--select", "I001,F401", "--fix", *fnames]
    )
    subprocess.check_call(["ruff", "format", "--silent", *fnames])
