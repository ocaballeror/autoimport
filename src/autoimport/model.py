"""Define the entities."""

import sys
import importlib.util
import inspect
import re
import pkgutil
from importlib import import_module
from types import ModuleType
from typing import Any, Dict, List, Optional, Set, Tuple

import autoflake
from pyflakes.messages import UndefinedExport, UndefinedName, UnusedImport
from pyprojroot import here

common_libraries = "typing", "common", "bodex"
common_statements: Dict[str, str] = {
    "ABC": "from abc import ABC",
    "BaseModel": "from pydantic import BaseModel",
    "Field": "from pydantic import Field",
    "ValidationError": "from pydantic import ValidationError",
    "BeautifulSoup": "from bs4 import BeautifulSoup",
    "Enum": "from enum import Enum",
    "MagicMock": "from unittest.mock import MagicMock",
    "Path": "from pathlib import Path",
    "StringIO": "from io import StringIO",
    "YAMLError": "from yaml import YAMLError",
    "abstractmethod": "from abc import abstractmethod",
    "config": "from decouple import config",
    "datetime": "from datetime import datetime",
    "logger": "from loguru import logger",
    "patch": "from unittest.mock import patch",
    "suppress": "from contextlib import suppress",
    "timedelta": "from datetime import datetime",
    "timezone": "from datetime import timezone",
    "tz": "from dateutil import tz",
}


# R0903: Too few public methods (1/2). We don't need more, but using the class instead
#   of passing the data between function calls is useful.
class SourceCode:  # noqa: R090
    """Python source code entity."""

    def __init__(
        self, source_code: str, config: Optional[Dict[str, Any]] = None
    ) -> None:
        """Initialize the object."""
        self.header: List[str] = []
        self.imports: List[str] = []
        self.typing: List[str] = []
        self.code: List[str] = []
        self.config: Dict[str, Any] = config if config else {}
        self._trailing_newline = False
        self._split_code(source_code)

    def fix(self) -> str:
        """Fix python source code to correct import statements.

        It corrects these errors:

            * Add missed import statements.
            * Remove unused import statements.
            * Move import statements to the top.
        """
        self._move_imports_to_top()
        self._fix_flake_import_errors()

        return self._join_code()

    def _split_code(self, source_code: str) -> None:
        """Split the source code in the different sections.

        * Module Docstring
        * Import statements
        * Typing statements
        * Code.

        Args:
            source_code: Source code to be corrected.
        """
        source_code_lines = source_code.splitlines()

        self._extract_header(source_code_lines)
        self._extract_import_statements(source_code_lines)
        self._extract_typing_statements(source_code_lines)
        self._extract_code(source_code_lines)
        if source_code.endswith("\n"):
            self._trailing_newline = True

    def _extract_header(self, source_lines: List[str]) -> None:
        """Save the module leading comments and docstring from the source code.

        Save them into self.header.

        Args:
            source_lines: A list containing all code lines.
        """
        docstring_type: Optional[str] = None

        for line in source_lines:
            if re.match(r'"{3}.*"{3}', line):
                # Match single line docstrings.
                self.header.append(line)
                break

            if docstring_type == "start_multiple_lines" and re.match(r'""" ?', line):
                # Match end of multiple line docstrings
                docstring_type = "multiple_lines"
            elif re.match(r'"{3}.*', line):
                # Match multiple line docstrings start
                docstring_type = "start_multiple_lines"
            elif re.match(r"#.*", line) or line == "":
                # Match leading comments and empty lines
                pass
            elif docstring_type in [None, "multiple_lines"]:
                break
            self.header.append(line)

    def _extract_import_statements(self, source_lines: List[str]) -> None:
        """Save the import statements from the source code into self.imports.

        Args:
            source_lines: A list containing all code lines.
        """
        import_start_line = len(self.header)
        multiline_import = False
        try_line: Optional[str] = None

        for line in source_lines[import_start_line:]:
            if re.match(r"^if TYPE_CHECKING:$", line):
                break
            if re.match(r"^(try|except.*):$", line):
                try_line = line
            elif (
                re.match(r"^\s*(from .*)?import.[^\'\"]*$", line)
                or line == ""
                or multiline_import
            ):
                # Process multiline import statements
                if "(" in line:
                    multiline_import = True
                elif ")" in line:
                    multiline_import = False

                if try_line:
                    self.imports.append(try_line)
                    try_line = None

                self.imports.append(line)
            else:
                break

    def _extract_typing_statements(self, source_lines: List[str]) -> None:
        """Save the typing statements from the source code into self.typing.

        Args:
            source_lines: A list containing all code lines.
        """
        typing_start_line = len(self.header) + len(self.imports)

        if typing_start_line < len(source_lines) and re.match(
            r"^if TYPE_CHECKING:$", source_lines[typing_start_line]
        ):
            self.typing.append(source_lines[typing_start_line])
            typing_start_line += 1
            for line in source_lines[typing_start_line:]:
                if not re.match(r"^\s+.*", line) and line != "":
                    break
                self.typing.append(line)

    def _extract_code(self, source_lines: List[str]) -> None:
        """Save the code from the source code into self.code.

        Args:
            source_lines: A list containing all code lines.
        """
        # Extract the code lines
        code_start_line = len(self.header) + len(self.imports) + len(self.typing)
        self.code = source_lines[code_start_line:]

    def _join_code(self) -> str:
        """Join the source code from docstring, import statements and code lines.

        Make sure that an empty line splits them.

        Returns:
            source_code: Source code to be corrected.
        """
        source_code = ""
        for section, new_lines in [
            ("header", 0),
            ("imports", 2),
            ("typing", 2),
            ("code", 3),
        ]:
            source_code = self._append_section(source_code, section, new_lines)

        # Remove possible new lines at the start of the document
        source_code = source_code.strip()

        # Respect the trailing newline
        if self._trailing_newline:
            source_code += "\n"

        return source_code

    def _append_section(
        self, source_code: str, section_name: str, empty_lines: int = 1
    ) -> str:
        """Append a section to the existent source code.

        Args:
            source_code: existing source code to append the new section.
            section_name: the source code section to append
            empty_lines: number of empty lines to append at the start.
        """
        section = getattr(self, section_name)

        if len(section) == 0 or section == [""]:
            return source_code

        source_code += "\n" * empty_lines + "\n".join(section).strip()

        return source_code

    @staticmethod
    def _should_ignore_line(line: str) -> bool:
        """Determine whether a line should be ignored by autoimport or not."""
        return any(
            [
                re.match(r".*?# ?fmt:.*?skip.*", line),
                re.match(r".*?# ?noqa:.*?autoimport.*", line),
            ]
        )

    def _move_imports_to_top(self) -> None:
        """Fix python source code to move import statements to the top of the file.

        Ignore the lines that contain the # noqa: autoimport string.
        """
        multiline_import = False
        multiline_string = False
        code_lines_to_remove = []

        for line_num, line in enumerate(self.code):
            # Process multiline strings, taking care not to catch single line strings
            # defined with three quotes.
            if re.match(r"^.*?(\"|\'){3}.*?(?!\1{3})$", line) and not re.match(
                r"^.*?(\"|\'){3}.*?\1{3}", line
            ):
                multiline_string = not multiline_string
                continue

            # Process import lines
            if (
                "=" not in line
                and not multiline_string
                and re.match(r"^\s*(?:from .*)?import .[^\'\"]*$", line)
            ) or multiline_import:
                if self._should_ignore_line(line):
                    continue

                # process lines using separation markers
                if ";" in line:
                    import_line, next_line = self._split_separation_line(line)
                    self.imports.append(import_line.strip())
                    self.code[line_num] = next_line
                    continue

                # Process multiline import statements
                if "(" in line:
                    multiline_import = True
                elif ")" in line:
                    multiline_import = False

                code_lines_to_remove.append(line)
                if not multiline_import:
                    line = line.strip()

                self.imports.append(line)

        for line in code_lines_to_remove:
            self.code.remove(line)

    @staticmethod
    def _split_separation_line(line: str) -> Tuple[str, str]:
        """Split separation lines into two and return both lines back."""
        first_line, next_line = line.split(";")
        # add correct number of leading spaces
        num_lspaces = len(first_line) - len(first_line.lstrip())
        next_line = f"{' ' * num_lspaces}{next_line.lstrip()}"
        return first_line, next_line

    def _fix_flake_import_errors(self) -> None:
        """Fix python source code to correct missed or unused import statements."""
        error_messages = autoflake.check(self._join_code())
        fixed_packages = []

        for message in error_messages:
            if isinstance(message, (UndefinedName, UndefinedExport)):
                object_name = message.message_args[0]
                if object_name not in fixed_packages:
                    self._add_package(object_name)
                    fixed_packages.append(object_name)
            elif isinstance(message, UnusedImport):
                import_name = message.message_args[0]
                self._remove_unused_imports(import_name)

    def _add_package(self, object_name: str) -> None:
        """Add a package to the source code.

        Args:
            object_name: Object name to search.
        """
        import_string = self._find_package(object_name)

        if import_string is not None:
            self.imports.append(import_string)

    def _find_package(self, name: str) -> Optional[str]:
        """Search package by an object's name.

        It will search in these places:

        * In the package we are developing.
        * Modules in PYTHONPATH.
        * Typing library.
        * Common statements.

        Args:
            name: Object name to search.

        Returns:
            import_string: String required to import the package.
        """
        for check in [
            "_find_package_in_common_statements",
            "_find_package_in_modules",
            "_find_package_in_libraries",
            "_find_package_in_our_project",
        ]:
            package = getattr(self, check)(name)
            if package is not None:
                return package
        return None

    @staticmethod
    def _find_project_packages() -> List[str]:
        return [
            path.name
            for path in here().iterdir()
            if path.is_dir()
            and path.name != "tests"
            and (path / "__init__.py").exists()
        ]

    def _find_package_in_our_project(self, name: str) -> Optional[str]:
        """Search the name in the objects of the package we are developing.

        Args:
            name: package name

        Returns:
            import_string: String required to import the package.
        """
        package_objects = {}

        if str(here()) not in sys.path:
            sys.path.append(str(here()))

        for package in self._find_project_packages():
            package_objects.update(extract_package_objects(package))

        # nocover: as the tests are run inside the autoimport virtualenv, it will
        # always find the objects on that package
        if package_objects is None:  # pragma: nocover
            return None
        try:
            return package_objects[name]
        except KeyError:
            return None

    @staticmethod
    def _find_package_in_modules(name: str) -> Optional[str]:
        """Search in the PYTHONPATH modules if object is a package.

        Args:
            name: package name

        Returns:
            import_string: String required to import the package.
        """
        package_specs = importlib.util.find_spec(name)

        try:
            importlib.util.module_from_spec(package_specs)  # type: ignore
        except AttributeError:
            return None

        return f"import {name}"

    @staticmethod
    def _find_package_in_libraries(name: str) -> Optional[str]:
        """Search in the typing library the object name.

        Args:
            name: package name

        Returns:
            import_string: Python 3.7 type checking compatible import string.
        """
        for lib in common_libraries:
            objects = extract_package_objects(lib)
            if name in objects:
                return objects[name]

        return None

    def _get_additional_statements(self) -> Dict[str, str]:
        """When parsing to the cli via --config-file the config becomes nested."""
        config_statements = self.config.get("common_statements")
        if config_statements:
            return config_statements
        return (
            self.config.get("tool", {}).get("autoimport", {}).get("common_statements")
        )

    def _find_package_in_common_statements(self, name: str) -> Optional[str]:
        """Search in the common statements the object name.

        Args:
            name: package name

        Returns:
            import_string
        """
        local_common_statements = common_statements.copy()
        additional_statements = self._get_additional_statements()
        if additional_statements:
            local_common_statements.update(additional_statements)

        if name in local_common_statements:
            return local_common_statements[name]

        return None

    def _remove_unused_imports(self, import_name: str) -> None:
        """Remove unused import statements.

        Args:
            import_name: Name of the imported object to remove.
        """
        package_name = ".".join(import_name.split(".")[:-1])
        object_name = import_name.split(".")[-1]

        for line in self.imports:
            if self._should_ignore_line(line):
                continue

            # If it's the only line, remove it
            if re.match(
                rf"(from {package_name} )?import ({package_name}\.)?{object_name}"
                rf"( *as [a-z]+)?( *#.*)?$",
                line,
            ):
                self.imports.remove(line)
                return
            # If it shares the line with other objects, just remove the unused one.
            if re.match(rf"from {package_name} import .*?{object_name}", line):
                # fmt: off
                # Format is required until there is no more need of the
                # experimental-string-processing flag of the Black formatter.
                match = re.match(
                    fr"(?P<from>from {package_name} import) "
                    fr"(?P<imports>[^#]*)(?P<comment>#.*)?",
                    line,
                )
                # fmt: on
                if match is not None:
                    line_number = self.imports.index(line)
                    imports = [
                        import_.strip() for import_ in match["imports"].split(", ")
                    ]
                    imports.remove(object_name)
                    new_imports = ", ".join(imports)
                    if match["comment"]:
                        new_imports += f'  {match["comment"]}'
                    self.imports[line_number] = f"{match['from']} {new_imports}"
                    return
            # If it's a multiline import statement
            elif re.match(
                rf"from {package_name} import .*?\($",
                line,
            ):
                line_number = self.imports.index(line)
                # Remove the object name from the multiline imports
                while line_number + 1 < len(self.imports):
                    line_number += 1
                    if re.match(rf"\s*?{object_name},?", self.imports[line_number]):
                        self.imports.pop(line_number)
                        break

                # Remove the whole import if there is no other object loaded
                if (
                    re.match(r"\s*from .* import", self.imports[line_number - 1])
                    and self.imports[line_number] == ")"
                ):
                    self.imports.pop(line_number)
                    self.imports.pop(line_number - 1)

                return


def import_submodules(module: ModuleType) -> Set[ModuleType]:
    """
    Import all modules from the specified root package and its subpackages recursively.
    """
    imported: Set[ModuleType] = set()
    if not hasattr(module, "__path__"):
        if hasattr(module, "__file__"):
            return {module}
        raise ValueError(f"Cannot find submodules of module {module}")

    for _, mod_name, ispkg in pkgutil.iter_modules(module.__path__):
        try:
            submodule = import_module(".".join((module.__name__, mod_name)))
        except Exception:
            continue

        imported.add(submodule)
        if ispkg:
            imported.update(import_submodules(submodule))

    return imported


def extract_package_objects(name: str) -> Dict[str, str]:
    """Extract the package objects and their import string.

    Returns:
        objects: A dictionary with the object name as a key and the import string
            as the value.
    """
    package_objects: Dict[str, str] = {}

    # Get the modules of the desired package
    try:
        root_module = importlib.import_module(name)

    except ModuleNotFoundError:
        return package_objects

    package_modules = list(import_submodules(root_module))

    # Get objects of the package
    for module in package_modules:
        for object_name, package_object in inspect.getmembers(module):
            if object_name.startswith("_") or object_name in package_objects:
                continue

            if hasattr(package_object, "__module__"):
                package_objects[
                    object_name
                ] = f"from {package_object.__module__} import {object_name}"

    return package_objects
