"""Define the entities."""

import ast
import hashlib
import importlib.util
import json
import pickle
import re
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from pyprojroot import here

common_libraries = ("typing",)
common_statements: dict[str, str] = {
    "ABC": "from abc import ABC",
    "BaseModel": "from pydantic import BaseModel",
    "Field": "from pydantic import Field",
    "ValidationError": "from pydantic import ValidationError",
    "BeautifulSoup": "from bs4 import BeautifulSoup",
    "Enum": "from enum import Enum",
    "MagicMock": "from unittest.mock import MagicMock",
    "Path": "from pathlib import Path",
    "StringIO": "from io import StringIO",
    "TYPE_CHECKING": "from typing import TYPE_CHECKING",
    "UUID": "from uuid import UUID",
    "YAMLError": "from yaml import YAMLError",
    "abstractmethod": "from abc import abstractmethod",
    "assert_never": "from typing import assert_never",
    "config": "from decouple import config",
    "datetime": "from datetime import datetime",
    "logger": "from loguru import logger",
    "patch": "from unittest.mock import patch",
    "suppress": "from contextlib import suppress",
    "timedelta": "from datetime import datetime",
    "timezone": "from datetime import timezone",
    "tz": "from dateutil import tz",
    "CustomContext": "from link_workflow.graphql.context import CustomContext",
}


# R0903: Too few public methods (1/2). We don't need more, but using the class instead
#   of passing the data between function calls is useful.
class SourceCode:  # noqa: R090
    """Python source code entity."""

    def __init__(
        self,
        source_code: str,
        filename: str = "<string>",
        config: dict[str, Any] | None = None,
    ) -> None:
        """Initialize the object."""
        self.header: list[str] = []
        self.imports: list[str] = []
        self.typing: list[str] = []
        self.code: list[str] = []
        self.filename: str = filename
        self.config: dict[str, Any] = config if config else {}
        self._trailing_newline = False
        self._split_code(source_code)
        self.cache_dir = Path(".autoimport_cache")
        self.cache_dir.mkdir(parents=True, exist_ok=True)

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

    def _extract_header(self, source_lines: list[str]) -> None:
        """Save the module leading comments and docstring from the source code.

        Save them into self.header.

        Args:
            source_lines: A list containing all code lines.
        """
        docstring_type: str | None = None

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

    def _extract_import_statements(self, source_lines: list[str]) -> None:
        """Save the import statements from the source code into self.imports.

        Args:
            source_lines: A list containing all code lines.
        """
        import_start_line = len(self.header)
        multiline_import = False
        try_line: str | None = None

        for line in source_lines[import_start_line:]:
            if re.match(r"^if TYPE_CHECKING:$", line):
                break
            if re.match(r"^(try|except.*):$", line):
                try_line = line
            elif (
                re.match(r"^\s*(from .*)?import.[^\'\"]*$", line) or line == "" or multiline_import
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

    def _extract_typing_statements(self, source_lines: list[str]) -> None:
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

    def _extract_code(self, source_lines: list[str]) -> None:
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

    def _append_section(self, source_code: str, section_name: str, empty_lines: int = 1) -> str:
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
        if self._get_disable_move_to_top():
            return
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
                and re.match(r"^(?:from .*)?import .[^\'\"]*$", line)
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
    def _split_separation_line(line: str) -> tuple[str, str]:
        """Split separation lines into two and return both lines back."""
        first_line, next_line = line.split(";")
        # add correct number of leading spaces
        num_lspaces = len(first_line) - len(first_line.lstrip())
        next_line = f"{' ' * num_lspaces}{next_line.lstrip()}"
        return first_line, next_line

    def _fix_flake_import_errors(self) -> None:
        """Fix python source code to correct missed or unused import statements."""
        source = self._join_code()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as tmp:
            tmp.write(source)
            tmp_path = Path(tmp.name)

        try:
            result = subprocess.run(
                [
                    "ruff",
                    "check",
                    "--select",
                    "F401,F821,F822",
                    "--output-format",
                    "json",
                    "--no-cache",
                    str(tmp_path),
                ],
                capture_output=True,
                text=True,
            )
            messages = json.loads(result.stdout) if result.stdout else []
        except Exception:
            return
        finally:
            tmp_path.unlink(missing_ok=True)

        fixed_packages = []
        for msg in messages:
            code = msg["code"]
            message = msg["message"]
            if code in ("F821", "F822"):
                match = re.search(r"`([^`]+)`", message)
                if match:
                    object_name = match.group(1)
                    if object_name not in fixed_packages:
                        self._add_package(object_name)
                        fixed_packages.append(object_name)
            elif code == "F401" and not self.keep_unused_imports:
                match = re.search(r"`([^`]+)`", message)
                if match:
                    self._remove_unused_imports(match.group(1))

    def _add_package(self, object_name: str) -> None:
        """Add a package to the source code.

        Args:
            object_name: Object name to search.
        """
        import_string = self._find_package(object_name)

        if import_string is not None:
            self.imports.append(import_string)

    def _find_package(self, name: str) -> str | None:
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

    def _find_project_packages(self, where: Path | None = None) -> list[str]:
        if not where:
            try:
                where = here()
            except RuntimeError:
                return []

        src = where / "src"
        if src.is_dir():
            return self._find_project_packages(src)

        return [
            path.name
            for path in where.iterdir()
            if path.is_dir() and path.name != "tests" and (path / "__init__.py").exists()
        ]

    def _find_usage(self, target: str) -> list[str]:
        mo = ast.parse(self._join_code())
        track = None
        uses = []
        for node in ast.walk(mo):
            if isinstance(node, ast.Assign):
                if isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name):
                    if isinstance(node.targets[0], ast.Name) and node.value.func.id == target:
                        track = node.targets[0].id

            if track and isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
                if isinstance(node.value.func, ast.Attribute) and isinstance(
                    node.value.func.value, ast.Name
                ):
                    if node.value.func.value.id == track:
                        uses.append(node.value.func.attr)

        return uses

    def _find_package_in_our_project(self, name: str) -> str | None:
        """Search the name in the objects of the package we are developing.

        Args:
            name: package name

        Returns:
            import_string: String required to import the package.
        """
        try:
            root = here()
        except RuntimeError:
            return None

        if str(root) not in sys.path:
            sys.path.append(str(root))

        candidates: list[tuple[str, Path]] = []
        for package in self._find_project_packages():
            objects, definition_files = self._extract_package_objects_with_files(package)
            if name in objects:
                for line, def_file in zip(objects[name], definition_files.get(name, [])):
                    candidates.append((line, def_file))

        if not candidates:
            return None

        import_lines = [line for line, _ in candidates]
        if len(set(import_lines)) == 1:
            return import_lines[0]

        return self._pick_best_candidate(name, candidates)

    def _pick_best_candidate(self, name: str, candidates: list[tuple[str, Path]]) -> str:
        import_lines = [line for line, _ in candidates]
        usage = self._find_usage(name)
        if not usage:
            return statistics.mode(import_lines)

        matching_candidates = []
        for line, def_file in candidates:
            attrs = self._parse_class_attributes(def_file, name)
            if all(attr in attrs for attr in usage):
                matching_candidates.append(line)

        if matching_candidates:
            return statistics.mode(matching_candidates)

        return statistics.mode(import_lines)

    @staticmethod
    def _find_package_in_modules(name: str) -> str | None:
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

    def _find_package_in_libraries(self, name: str) -> str | None:
        """Search in the typing library the object name.

        Args:
            name: package name

        Returns:
            import_string: Python 3.7 type checking compatible import string.
        """
        for lib in common_libraries:
            objects = self.extract_package_objects(lib)
            if name in objects:
                return objects[name][0]

        return None

    def _get_disable_move_to_top(self) -> bool:
        """Fetch the disable_move_to_top configuration value."""
        # When parsing to the cli via --config-file the config becomes nested.
        disable_move_to_top = self.config.get("disable_move_to_top")
        if disable_move_to_top is not None:
            return disable_move_to_top
        return self.config.get("tool", {}).get("autoimport", {}).get("disable_move_to_top", False)

    def _get_additional_statements(self) -> dict[str, str]:
        """Fetch the common_statements configuration value."""
        # When parsing to the cli via --config-file the config becomes nested.
        config_statements = self.config.get("common_statements")
        if config_statements:
            return config_statements
        return self.config.get("tool", {}).get("autoimport", {}).get("common_statements")

    def _find_package_in_common_statements(self, name: str) -> str | None:
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
                    imports = [import_.strip() for import_ in match["imports"].split(", ")]
                    imports.remove(object_name)
                    new_imports = ", ".join(imports)
                    if match["comment"]:
                        new_imports += f"  {match['comment']}"
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

    def get_cache_path(self, package_name: str) -> Path:
        hash_name = hashlib.sha256(package_name.encode()).hexdigest()
        return self.cache_dir / f"{hash_name}.pkl"

    def _iter_package_files(self, package_name: str) -> dict[str, tuple[Path, bool]]:
        """Return {module_name: (file_path, is_init)} for every .py file in the package."""
        parts = package_name.split(".")
        base_path = None
        path_entry_path = None
        for path_entry in sys.path:
            candidate = Path(path_entry, *parts)
            if candidate.is_dir():
                base_path = candidate
                path_entry_path = Path(path_entry)
                break
        if base_path is None:
            return {}

        result: dict[str, tuple[Path, bool]] = {}
        for py_file in base_path.rglob("*.py"):
            rel_parts = list(py_file.relative_to(path_entry_path).with_suffix("").parts)
            is_init = rel_parts[-1] == "__init__"
            if is_init:
                mod_name = ".".join(rel_parts[:-1])
            else:
                if rel_parts[-1].startswith("_"):
                    continue
                mod_name = ".".join(rel_parts)
            result[mod_name] = (py_file, is_init)

        return result

    @staticmethod
    def _parse_module_definitions(file: Path) -> set[str]:
        """Return all importable top-level names defined in file (AST, no import)."""
        try:
            tree = ast.parse(file.read_text())
        except Exception:
            return set()

        names: set[str] = set()
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if not node.name.startswith("_"):
                    names.add(node.name)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and not target.id.startswith("_"):
                        names.add(target.id)
            elif isinstance(node, ast.AnnAssign):
                if isinstance(node.target, ast.Name) and not node.target.id.startswith("_"):
                    names.add(node.target.id)
            elif isinstance(node, ast.TypeAlias):
                if isinstance(node.name, ast.Name) and not node.name.id.startswith("_"):
                    names.add(node.name.id)
        return names

    @staticmethod
    def _parse_init_reexports(file: Path, init_module: str) -> dict[str, str]:
        """Return {exported_name: source_module} for relative imports in an __init__.py."""
        try:
            tree = ast.parse(file.read_text())
        except Exception:
            return {}

        module_parts = init_module.split(".")
        reexports: dict[str, str] = {}

        for node in tree.body:
            if not isinstance(node, ast.ImportFrom) or node.level == 0:
                continue
            level = node.level
            if level > len(module_parts):
                continue
            base_parts = module_parts[: len(module_parts) - (level - 1)]
            source = ".".join(base_parts) + ("." + node.module if node.module else "")
            for alias in node.names:
                if alias.name == "*" or alias.asname is not None:
                    continue
                reexports[alias.name] = source

        return reexports

    @staticmethod
    def _parse_class_attributes(file: Path, class_name: str) -> set[str]:
        """Return method and attribute names declared in class_name inside file."""
        try:
            tree = ast.parse(file.read_text())
        except Exception:
            return set()

        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef) or node.name != class_name:
                continue
            attrs: set[str] = set()
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    attrs.add(item.name)
                    for subnode in ast.walk(item):
                        if isinstance(subnode, ast.Assign):
                            for target in subnode.targets:
                                if (
                                    isinstance(target, ast.Attribute)
                                    and isinstance(target.value, ast.Name)
                                    and target.value.id == "self"
                                ):
                                    attrs.add(target.attr)
                        elif isinstance(subnode, ast.AnnAssign):
                            if (
                                isinstance(subnode.target, ast.Attribute)
                                and isinstance(subnode.target.value, ast.Name)
                                and subnode.target.value.id == "self"
                            ):
                                attrs.add(subnode.target.attr)
                elif isinstance(item, ast.Assign):
                    for target in item.targets:
                        if isinstance(target, ast.Name):
                            attrs.add(target.id)
                elif isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                    attrs.add(item.target.id)
            return attrs
        return set()

    @staticmethod
    def _parent_packages(module: str) -> list[str]:
        """Return ancestor package names from immediate parent to root."""
        parts = module.split(".")
        return [".".join(parts[:i]) for i in range(len(parts) - 1, 0, -1)]

    def extract_package_objects(self, package_name: str) -> dict[str, list[str]]:
        objects, self._definition_files = self._extract_package_objects_with_files(package_name)
        return objects

    def _extract_package_objects_with_files(
        self, package_name: str
    ) -> tuple[dict[str, list[str]], dict[str, list[Path]]]:
        cache_path = self.get_cache_path(package_name)
        all_files = self._iter_package_files(package_name)

        if not all_files:
            return {}, {}

        cached_module_defs: dict[str, Any] = {}
        cached_init_reexports: dict[str, Any] = {}
        if cache_path.exists():
            try:
                data = pickle.loads(cache_path.read_bytes())
                cached_module_defs = data.get("module_defs", {})
                cached_init_reexports = data.get("init_reexports", {})
            except Exception:
                pass

        module_defs: dict[str, list[str]] = {}
        init_reexports: dict[str, dict[str, str]] = {}
        new_module_defs: dict[str, Any] = {}
        new_init_reexports: dict[str, Any] = {}
        dirty = False

        for mod_name, (file_path, is_init) in all_files.items():
            mtime = file_path.stat().st_mtime
            if is_init:
                cached = cached_init_reexports.get(mod_name, {})
                if cached.get("mtime", 0) >= mtime:
                    init_reexports[mod_name] = cached["reexports"]
                else:
                    init_reexports[mod_name] = self._parse_init_reexports(file_path, mod_name)
                    dirty = True
                new_init_reexports[mod_name] = {
                    "mtime": mtime,
                    "reexports": init_reexports[mod_name],
                }
            else:
                cached = cached_module_defs.get(mod_name, {})
                if cached.get("mtime", 0) >= mtime:
                    module_defs[mod_name] = cached["names"]
                else:
                    module_defs[mod_name] = list(self._parse_module_definitions(file_path))
                    dirty = True
                new_module_defs[mod_name] = {"mtime": mtime, "names": module_defs[mod_name]}

        if dirty:
            cache_path.write_bytes(
                pickle.dumps({"module_defs": new_module_defs, "init_reexports": new_init_reexports})
            )

        objects: dict[str, list[str]] = {}
        definition_files: dict[str, list[Path]] = {}

        for mod_name, names in module_defs.items():
            file_path = all_files[mod_name][0]
            for name in names:
                current = mod_name
                for ancestor in self._parent_packages(mod_name):
                    if init_reexports.get(ancestor, {}).get(name) == current:
                        current = ancestor
                    else:
                        break
                objects.setdefault(name, []).append(f"from {current} import {name}")
                definition_files.setdefault(name, []).append(file_path)

        return objects, definition_files
