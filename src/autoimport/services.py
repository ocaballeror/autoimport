"""Define all the orchestration functionality required by the program to work.

Classes and functions that connect the different domain model objects with the adapters
and handlers to achieve the program's purpose.
"""

import shutil
import subprocess
from _io import TextIOWrapper
from typing import Any

from autoimport.model import SourceCode


def isort(files: tuple[TextIOWrapper, ...]) -> None:
    if shutil.which("ruff"):
        subprocess.run(["ruff", "check", "--silent", "--fix", *(f.name for f in files)])
        subprocess.run(["ruff", "format", "--silent", *(f.name for f in files)])

    elif shutil.which("isort"):
        subprocess.run(["isort", *(f.name for f in files)])


def fix_files(
    files: tuple[TextIOWrapper, ...],
    config: dict[str, Any] | None = None,
    keep_unused_imports: bool = False,
) -> None:
    """Fix the python source code of a list of files.

    If the input is taken from stdin, it will output the value to stdout.

    Args:
        files: List of files to fix.

    Returns:
        Fixed code retrieved from stdin or None.
    """
    for file_wrapper in files:
        source = file_wrapper.read()
        fixed_source = fix_code(source, file_wrapper.name, config, keep_unused_imports)

        if fixed_source == source and file_wrapper.name != "<stdin>":
            continue

        file_wrapper.seek(0)
        file_wrapper.write(fixed_source)
        file_wrapper.truncate()
        file_wrapper.close()

    isort(files)


def fix_code(
    original_source_code: str,
    filename: str,
    config: dict[str, Any] | None = None,
    keep_unused_imports: bool = False,
) -> str:
    """Fix python source code to correct import statements.

    It corrects these errors:

        * Add missed import statements.
        * Remove unused import statements.
        * Move import statements to the top.

    Args:
        original_source_code: Source code to be corrected.
        keep_unused_imports: If true, unused imports are retained.

    Returns:
        Corrected source code.
    """
    return SourceCode(
        original_source_code,
        filename=filename,
        config=config,
        keep_unused_imports=keep_unused_imports,
    ).fix()
