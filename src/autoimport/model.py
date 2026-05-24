"""Define the entities."""

import ast
import hashlib
import importlib.metadata
import importlib.util
import pickle
import statistics
import sys
import tomllib
from collections import defaultdict
from functools import cache
from pathlib import Path
from typing import Any, NamedTuple

from pyprojroot import here


class _CallInfo(NamedTuple):
    positional_types: list[str | None]
    keyword_names: frozenset[str]


class _MethodSig(NamedTuple):
    param_types: list[str | None]
    min_positional: int
    max_positional: int | None  # None = unlimited via *args
    all_param_names: frozenset[str]
    has_var_keyword: bool


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
        self._pkg_cache: dict[str, tuple[dict[str, list[str]], dict[str, list[Path]]]] = {}

    def index_packages(self, names: list[str]) -> None:
        try:
            root = here()
        except RuntimeError:
            return

        if str(root) not in sys.path:
            sys.path.append(str(root))

        all_packages = list(self._find_project_packages()) + self._read_project_dependencies(root)

        for package in all_packages:
            objects, def_files = self._extract_package_objects_with_files(package)
            for obj, imports in objects.items():
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
        if where is None:
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

    @staticmethod
    @cache
    def _read_project_dependencies(root: Path) -> list[str]:
        try:
            with open(root / "pyproject.toml", "rb") as f:
                data = tomllib.load(f)
            raw_deps = data.get("project", {}).get("dependencies", [])
        except Exception:
            return []

        dist_names: list[str] = []
        for dep in raw_deps:
            name = dep.strip()
            for i, ch in enumerate(name):
                if ch in "><=!;[ \t":
                    name = name[:i]
                    break
            if name:
                dist_names.append(name)

        try:
            pkg_dist = importlib.metadata.packages_distributions()
            dist_to_import: dict[str, str] = {}
            for pkg_name, dists in pkg_dist.items():
                for d in dists:
                    dist_to_import[d.lower().replace("-", "_")] = pkg_name
            return [
                dist_to_import.get(d.lower().replace("-", "_"), d.lower().replace("-", "_"))
                for d in dist_names
            ]
        except Exception:
            return [d.lower().replace("-", "_") for d in dist_names]

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

            if track and isinstance(node, ast.Expr):
                if (
                    isinstance(node.value, ast.Call)
                    and isinstance(node.value.func, ast.Attribute)
                    and isinstance(node.value.func.value, ast.Name)
                    and node.value.func.value.id == track
                ):
                    uses.append(node.value.func.attr)
                elif (
                    isinstance(node.value, ast.Attribute)
                    and isinstance(node.value.value, ast.Name)
                    and node.value.value.id == track
                ):
                    uses.append(node.value.attr)

        return uses

    def _find_package_in_our_project(self, name: str, file: Path) -> str | None:
        if name not in self.import_cache:
            self.index_packages([name])

        candidates = self.import_cache[name]
        if not candidates:
            return None
        elif len(candidates) == 1:
            return list(candidates)[0][0]

        usage = self._find_usage(file, name)
        method_calls = self._find_method_calls(file, name)

        if not usage and not method_calls:
            return statistics.mode([line for line, _ in candidates])

        attr_filtered = [
            (line, def_file) for line, def_file in candidates
            if not usage or all(attr in self._parse_class_attributes(def_file, name) for attr in usage)
        ]

        if not attr_filtered:
            return statistics.mode([line for line, _ in candidates])

        if len(attr_filtered) == 1 or not method_calls:
            return statistics.mode([line for line, _ in attr_filtered])

        sig_filtered = [
            line for line, def_file in attr_filtered
            if self._call_signatures_match(
                method_calls, self._parse_method_signatures(def_file, name)
            )
        ]

        if sig_filtered:
            return statistics.mode(sig_filtered)

        return statistics.mode([line for line, _ in attr_filtered])

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
    def _parse_module_definitions(file: Path, module: str) -> tuple[set[str], dict[str, str]]:
        try:
            tree = ast.parse(file.read_text())
        except Exception:
            return set(), {}

        module_parts = module.split(".")

        names: set[str] = set()
        reexports: dict[str, str] = {}
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
            elif isinstance(node, ast.ImportFrom):
                if node.level == 0:
                    source = node.module
                else:
                    # Relative import: resolve against the current module's package.
                    # Init files represent the package itself, so level=1 means "this package";
                    # non-init files need to go up one extra level.
                    is_init = file.name == "__init__.py"
                    strip = node.level - (1 if is_init else 0)
                    base_parts = module_parts[:-strip] if strip > 0 else module_parts
                    if not base_parts:
                        continue
                    source = ".".join(base_parts) + ("." + node.module if node.module else "")

                for alias in node.names:
                    if alias.name == "*" or alias.asname is not None:
                        continue
                    reexports[alias.name] = source
        return names, reexports

    @staticmethod
    def _parse_class_attributes(file: Path, class_name: str) -> set[str]:
        try:
            tree = ast.parse(file.read_text())
        except Exception:
            return set()

        classes: dict[str, ast.ClassDef] = {
            node.name: node
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef)
        }

        def extract_attrs(node: ast.ClassDef, seen: set[str]) -> set[str]:
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
            for base in node.bases:
                if isinstance(base, ast.Name) and base.id in classes and base.id not in seen:
                    seen.add(base.id)
                    attrs |= extract_attrs(classes[base.id], seen)
            return attrs

        if class_name not in classes:
            return set()

        return extract_attrs(classes[class_name], {class_name})

    @staticmethod
    def _infer_arg_type(node: ast.expr) -> str | None:
        if isinstance(node, ast.Constant):
            return type(node.value).__name__
        if isinstance(node, ast.List):
            return "list"
        if isinstance(node, ast.Dict):
            return "dict"
        if isinstance(node, ast.Set):
            return "set"
        if isinstance(node, ast.Tuple):
            return "tuple"
        return None

    @staticmethod
    def _find_method_calls(file: Path, target: str) -> dict[str, _CallInfo]:
        try:
            mo = ast.parse(file.read_text(), file.name)
        except SyntaxError:
            return {}

        tracked: set[str] = set()
        result: dict[str, _CallInfo] = {}

        for node in ast.walk(mo):
            if isinstance(node, ast.Assign):
                if (
                    isinstance(node.value, ast.Call)
                    and isinstance(node.value.func, ast.Name)
                    and node.value.func.id == target
                    and isinstance(node.targets[0], ast.Name)
                ):
                    tracked.add(node.targets[0].id)

            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                value = node.func.value
                is_tracked = isinstance(value, ast.Name) and value.id in tracked
                is_direct = (
                    isinstance(value, ast.Call)
                    and isinstance(value.func, ast.Name)
                    and value.func.id == target
                )
                if is_tracked or is_direct:
                    method = node.func.attr
                    if method not in result:
                        result[method] = _CallInfo(
                            positional_types=[
                                PackageFinder._infer_arg_type(arg) for arg in node.args
                            ],
                            keyword_names=frozenset(
                                kw.arg for kw in node.keywords if kw.arg is not None
                            ),
                        )

        return result

    @staticmethod
    def _parse_method_signatures(file: Path, class_name: str) -> dict[str, _MethodSig]:
        try:
            tree = ast.parse(file.read_text())
        except Exception:
            return {}

        classes: dict[str, ast.ClassDef] = {
            node.name: node
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef)
        }

        def get_annotation(ann: ast.expr | None) -> str | None:
            return ann.id if isinstance(ann, ast.Name) else None

        def sig_from_func(func: ast.FunctionDef | ast.AsyncFunctionDef) -> _MethodSig:
            positional = func.args.args[1:]  # exclude self
            num_defaults = len(func.args.defaults)
            return _MethodSig(
                param_types=[get_annotation(p.annotation) for p in positional],
                min_positional=len(positional) - num_defaults,
                max_positional=None if func.args.vararg else len(positional),
                all_param_names=frozenset(p.arg for p in positional)
                | frozenset(p.arg for p in func.args.kwonlyargs),
                has_var_keyword=func.args.kwarg is not None,
            )

        def extract(node: ast.ClassDef, seen: set[str]) -> dict[str, _MethodSig]:
            sigs: dict[str, _MethodSig] = {}
            for base in node.bases:
                if isinstance(base, ast.Name) and base.id in classes and base.id not in seen:
                    seen.add(base.id)
                    sigs.update(extract(classes[base.id], seen))
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    sigs[item.name] = sig_from_func(item)
            return sigs

        if class_name not in classes:
            return {}

        return extract(classes[class_name], {class_name})

    @staticmethod
    def _call_signatures_match(
        calls: dict[str, _CallInfo],
        sigs: dict[str, _MethodSig],
    ) -> bool:
        for method, call in calls.items():
            if method not in sigs:
                continue
            sig = sigs[method]
            n = len(call.positional_types)
            if n < sig.min_positional:
                return False
            if sig.max_positional is not None and n > sig.max_positional:
                return False
            if not sig.has_var_keyword and not call.keyword_names.issubset(sig.all_param_names):
                return False
            for i, call_type in enumerate(call.positional_types):
                if call_type is None or i >= len(sig.param_types) or sig.param_types[i] is None:
                    continue
                if call_type != sig.param_types[i]:
                    return False
        return True

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
        if package_name in self._pkg_cache:
            return self._pkg_cache[package_name]

        result = self._load_package_objects(package_name)
        self._pkg_cache[package_name] = result
        return result

    def _load_package_objects(
        self, package_name: str
    ) -> tuple[dict[str, list[str]], dict[str, list[Path]]]:
        cache_path = self.get_cache_path(package_name)
        all_files = self._iter_package_files(package_name)

        if not all_files:
            return {}, {}

        current_fp = {mod: path.stat().st_mtime for mod, (path, _) in all_files.items()}

        cached: dict[str, Any] = {}
        if cache_path.exists():
            try:
                cached = pickle.loads(cache_path.read_bytes())
            except Exception:
                cache_path.unlink()

        if cached.get("fingerprint") == current_fp and "objects" in cached:
            return cached["objects"], cached["def_files"]

        cached_modules = cached.get("modules", {})
        module_defs: dict[str, set[str]] = {}
        module_reexports: dict[str, dict[str, str]] = {}

        for mod_name, (file_path, _) in all_files.items():
            entry = cached_modules.get(mod_name, {})
            if entry.get("mtime", 0) >= current_fp[mod_name]:
                module_defs[mod_name] = entry["names"]
                module_reexports[mod_name] = entry.get("reexports", {})
            else:
                module_defs[mod_name], module_reexports[mod_name] = (
                    self._parse_module_definitions(file_path, mod_name)
                )

        objects: dict[str, list[str]] = {}
        definition_files: dict[str, list[Path]] = {}

        for mod_name, names in module_defs.items():
            file_path = all_files[mod_name][0]
            for name in names:
                current = mod_name
                for ancestor in self._parent_packages(mod_name):
                    if module_reexports.get(ancestor, {}).get(name) == current:
                        current = ancestor
                    else:
                        break
                objects.setdefault(name, []).append(f"from {current} import {name}")
                definition_files.setdefault(name, []).append(file_path)

        cache_path.write_bytes(
            pickle.dumps({
                "fingerprint": current_fp,
                "objects": objects,
                "def_files": definition_files,
                "modules": {
                    mod_name: {
                        "mtime": current_fp[mod_name],
                        "names": module_defs[mod_name],
                        "reexports": module_reexports[mod_name],
                    }
                    for mod_name in all_files
                },
            })
        )

        return objects, definition_files
