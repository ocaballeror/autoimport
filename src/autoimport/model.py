"""Define the entities."""

import ast
import hashlib
import importlib.util
import pickle
import statistics
import sys
from collections import defaultdict
from functools import cache
from pathlib import Path
from typing import Any

from pyprojroot import here

common_libraries = ("typing",)
common_statements: dict[str, str] = {
    "ABC": "from abc import ABC",
    "AsyncIterator": "from collections.abc import AsyncIterator",
    "AsyncMock": "from unittest.mock import AsyncMock",
    "BaseModel": "from pydantic import BaseModel",
    "BeautifulSoup": "from bs4 import BeautifulSoup",
    "Callable": "from collections.abc import Callable",
    "Depends": "from fastapi import Depends",
    "Enum": "from enum import Enum",
    "Enum": "from enum import Enum",
    "Field": "from pydantic import Field",
    "Image": "from PIL import Image",
    "Iterator": "from collections.abc import Iterator",
    "MagicMock": "from unittest.mock import MagicMock",
    "NamedTemporaryFile": "from tempfile import NamedTemporaryFile",
    "Path": "from pathlib import Path",
    "StringIO": "from io import StringIO",
    "TYPE_CHECKING": "from typing import TYPE_CHECKING",
    "TemporaryDirectory": "from tempfile import TemporaryDirectory",
    "UUID": "from uuid import UUID",
    "ValidationError": "from pydantic import ValidationError",
    "YAMLError": "from yaml import YAMLError",
    "abstractmethod": "from abc import abstractmethod",
    "assert_never": "from typing import assert_never",
    "asynccontextmanager": "from contextlib import asynccontextmanager",
    "config": "from decouple import config",
    "cv": "import cv2 as cv",
    "datetime": "from datetime import datetime",
    "logger": "from loguru import logger",
    "patch": "from unittest.mock import patch",
    "sp": "import subprocess as sp",
    "suppress": "from contextlib import suppress",
    "timedelta": "from datetime import timedelta",
    "timezone": "from datetime import timezone",
    "tz": "from dateutil import tz",
}


class PackageFinder:
    """Finds the correct import statement for a given object name."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config if config else {}
        self.cache_dir = Path(".autoimport_cache")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.import_cache: dict[str, set[tuple[str, str]]] = defaultdict(set)

    def index_packages(self, names: list[str]) -> None:
        try:
            root = here()
        except RuntimeError:
            return

        if str(root) not in sys.path:
            sys.path.append(str(root))

        for package in self._find_project_packages():
            objects, def_files = self._extract_package_objects_with_files(package)
            assert objects.keys() == def_files.keys()
            for obj, imports in objects.items():
                assert len(imports) == len(def_files[obj])
                if obj in names:
                    self.import_cache[obj].update(set(zip(imports, def_files[obj])))

    def find_package(self, name: str, file: Path) -> str | None:
        for check in [
            self._find_package_in_common_statements,
            self._find_package_in_modules,
            self._find_package_in_libraries,
        ]:
            package = check(name)
            if package is not None:
                return package

        return self._find_package_in_our_project(name, file)

    @cache
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

    def _find_usage(self, file: Path, target: str) -> list[str]:
        try:
            mo = ast.parse(file.read_text(), file.name)
        except SyntaxError:
            return []
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

    def _find_package_in_our_project(self, name: str, file: Path) -> str | None:
        if not self.import_cache:
            self.index_packages([name])

        candidates = self.import_cache[name]
        if not candidates:
            return None
        elif len(candidates) == 1:
            return list(candidates)[0]

        usage = self._find_usage(file, name)
        if not usage:
            return statistics.mode([line for line, _ in candidates])

        matching_candidates = []
        for line, def_file in candidates:
            attrs = self._parse_class_attributes(def_file, name)
            if all(attr in attrs for attr in usage):
                matching_candidates.append(line)

        if matching_candidates:
            return statistics.mode(matching_candidates)

        return statistics.mode([line for line, _ in candidates])

    @staticmethod
    @cache
    def _find_package_in_modules(name: str) -> str | None:
        package_specs = importlib.util.find_spec(name)

        try:
            importlib.util.module_from_spec(package_specs)  # type: ignore
        except AttributeError:
            return None

        return f"import {name}"

    @cache
    def _find_package_in_libraries(self, name: str) -> str | None:
        for lib in common_libraries:
            objects = self.extract_package_objects(lib)
            if name in objects:
                return objects[name][0]

        return None

    def _get_additional_statements(self) -> dict[str, str] | None:
        config_statements = self.config.get("common_statements")
        if config_statements:
            return config_statements
        return self.config.get("tool", {}).get("autoimport", {}).get("common_statements")

    @cache
    def _find_package_in_common_statements(self, name: str) -> str | None:
        local_common_statements = common_statements.copy()
        additional_statements = self._get_additional_statements()
        if additional_statements:
            local_common_statements.update(additional_statements)

        if name in local_common_statements:
            return local_common_statements[name]

        return None

    def get_cache_path(self, package_name: str) -> Path:
        hash_name = hashlib.sha256(package_name.encode()).hexdigest()
        return self.cache_dir / f"{hash_name}.pkl"

    def _iter_package_files(self, package_name: str) -> dict[str, tuple[Path, bool]]:
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
        parts = module.split(".")
        return [".".join(parts[:i]) for i in range(len(parts) - 1, 0, -1)]

    def extract_package_objects(self, package_name: str) -> dict[str, list[str]]:
        objects, _ = self._extract_package_objects_with_files(package_name)
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
