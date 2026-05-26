"""File manipulation helpers: insert, delete, and protect import lines."""

import ast
import re
from collections.abc import Sequence
from pathlib import Path
from tempfile import NamedTemporaryFile


def _find_header_end(lines: list[str]) -> int:
    """Return the line index after any shebang, leading comments, and module docstring."""
    i = 0
    n = len(lines)

    while i < n and (lines[i].startswith("#") or not lines[i].strip()):
        i += 1

    source = "".join(lines)
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return i

    if (
        tree.body
        and isinstance(tree.body[0], ast.Expr)
        and isinstance(tree.body[0].value, ast.Constant)
        and isinstance(tree.body[0].value.value, str)
    ):
        # end_lineno is 1-indexed; that value == the 0-indexed position of the next line
        return tree.body[0].end_lineno  # type: ignore[return-value]

    return i


def insert_imports(file: Path, imports: list[str]) -> None:
    """Insert import statements after the file header (shebang, comments, docstring)."""
    source = file.read_text()
    lines = source.splitlines(keepends=True)
    i = _find_header_end(lines)

    import_block = "\n".join(imports) + "\n"
    before = "".join(lines[:i])
    rest = "".join(lines[i:])

    if before:
        trailing_newlines = len(before) - len(before.rstrip("\n"))
        if trailing_newlines < 2:
            import_block = "\n" * (2 - trailing_newlines) + import_block

    source = before + import_block + rest
    file.write_text(source)


def delete_lines(path: Path, line_numbers: set[int]) -> list[str]:
    removed = []
    with NamedTemporaryFile(
        mode="w", delete=False, encoding="utf-8", dir=path.parent, suffix=".tmp"
    ) as tmp:
        with path.open("r", encoding="utf-8") as src:
            for idx, line in enumerate(src, start=1):
                if idx in line_numbers:
                    removed.append(line.rstrip("\n"))
                else:
                    tmp.write(line)

    Path(tmp.name).replace(path)
    return removed


_COMPOUND_FMT_SKIP = re.compile(
    r"^(\s*)(?:import\s+\w+|from\s+[\w.]+\s+import\s+\w+)\s*;.*#\s*fmt:\s*skip\s*$"
)
_PLACEHOLDER_RE = re.compile(r"^\s*pass  # _autoimport_save_(\d+)\s*$")


def stash_compound_fmt_skip(files: Sequence[Path]) -> dict[Path, dict[int, str]]:
    """Replace compound `import X; ...  # fmt: skip` lines with `pass` placeholders.

    ruff format splits compound statements even when marked # fmt: skip, which
    would cause autoimport to move the import to the top. Replacing the compound
    line with an inert `pass` placeholder prevents ruff from touching it, and the
    original line is restored after all processing.
    """
    stashed: dict[Path, dict[int, str]] = {}
    for path in files:
        try:
            lines = path.read_text().splitlines(keepends=True)
            new_lines: list[str] = []
            path_stash: dict[int, str] = {}
            for line in lines:
                m = _COMPOUND_FMT_SKIP.match(line.rstrip("\n"))
                if m:
                    idx = len(path_stash)
                    indent = m.group(1)
                    path_stash[idx] = line if line.endswith("\n") else line + "\n"
                    new_lines.append(f"{indent}pass  # _autoimport_save_{idx}\n")
                else:
                    new_lines.append(line)
            if path_stash:
                stashed[path] = path_stash
                path.write_text("".join(new_lines))
        except OSError:
            pass
    return stashed


def restore_compound_fmt_skip(files: Sequence[Path], stashed: dict[Path, dict[int, str]]) -> None:
    """Restore compound `import X; ...  # fmt: skip` lines from placeholders."""
    for path in files:
        if path not in stashed:
            continue
        path_stash = stashed[path]
        try:
            lines = path.read_text().splitlines(keepends=True)
            new_lines: list[str] = []
            for line in lines:
                m = _PLACEHOLDER_RE.match(line.rstrip("\n"))
                if m:
                    idx = int(m.group(1))
                    original = path_stash.get(idx)
                    new_lines.append(original if original is not None else line)
                else:
                    new_lines.append(line)
            path.write_text("".join(new_lines))
        except OSError:
            pass
