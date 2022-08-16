"""Define all the orchestration functionality required by the program to work.

Classes and functions that connect the different domain model objects with the adapters
and handlers to achieve the program's purpose.
"""

import shutil
import subprocess
from typing import Any, Dict, Optional, Tuple

from _io import TextIOWrapper

from autoimport.model import SourceCode


def isort(files: Tuple[TextIOWrapper, ...]) -> None:
    if shutil.which("isort"):
        subprocess.run(["isort", *(f.name for f in files)])


def fix_files(
    files: Tuple[TextIOWrapper, ...], config: Optional[Dict[str, Any]] = None
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
        fixed_source = fix_code(source, config)

        if fixed_source == source and file_wrapper.name != "<stdin>":
            continue

        file_wrapper.seek(0)
        file_wrapper.write(fixed_source)
        file_wrapper.truncate()
        file_wrapper.close()

    isort(files)


def fix_code(original_source_code: str, config: Optional[Dict[str, Any]] = None) -> str:
    """Fix python source code to correct import statements.

    It corrects these errors:

        * Add missed import statements.
        * Remove unused import statements.
        * Move import statements to the top.

    Args:
        original_source_code: Source code to be corrected.

    Returns:
        Corrected source code.
    """
    return SourceCode(original_source_code, config=config).fix()
