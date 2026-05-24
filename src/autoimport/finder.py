"""Import resolution: find the correct import statement for a given name."""

import hashlib
import importlib.metadata
import importlib.util
import pickle
import statistics
import sys
import tomllib
from collections import defaultdict
from collections.abc import Iterable
from functools import cache
from pathlib import Path
from typing import Any

from pyprojroot import here

from autoimport.ast_utils import (
    analyze_usage,
    call_signatures_match,
    parse_class_attributes,
    parse_method_signatures,
    parse_module_definitions,
)
from autoimport.constants import common_libraries, common_statements

# Fallback mapping for distributions whose import name cannot be derived from
# the distribution name by the standard lowercase/underscore heuristic, used
# only when importlib.metadata.packages_distributions has no entry for the dist
# (typically: dependency is declared in pyproject but not installed locally).
_DIST_FALLBACK_MAP: dict[str, str] = {
    "pyyaml": "yaml",
    "python_dateutil": "dateutil",
    "pillow": "PIL",
    "beautifulsoup4": "bs4",
    "scikit_learn": "sklearn",
    "scikit_image": "skimage",
    "msgpack_python": "msgpack",
    "opencv_python": "cv2",
    "opencv_python_headless": "cv2",
    "opencv_contrib_python": "cv2",
    "python_jose": "jose",
    "python_magic": "magic",
    "grpcio": "grpc",
    "psycopg2_binary": "psycopg2",
    "discord_py": "discord",
    "google_cloud_storage": "google.cloud.storage",
    "google_cloud_bigquery": "google.cloud.bigquery",
    "protobuf": "google.protobuf",
}


class PackageFinder:
    """Finds the correct import statement for a given object name."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config if config else {}
        try:
            root = here()
        except RuntimeError:
            root = Path()
        self.cache_dir = root / ".autoimport_cache"
        self.import_cache: dict[str, set[tuple[str, Path]]] = defaultdict(set)
        self._pkg_cache: dict[str, tuple[dict[str, list[str]], dict[str, list[Path]]]] = {}
        self._common_stmt_cache: dict[str, str | None] = {}
        self._libraries_cache: dict[str, str | None] = {}
        self._project_packages_cache: dict[Path | None, list[str]] = {}

    def index_packages(self, names: Iterable[str]) -> None:
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
        for check in (
            self._find_package_in_common_statements,
            self._find_package_in_modules,
            self._find_package_in_libraries,
        ):
            package = check(name)
            if package is not None:
                return package
        return self._find_package_in_our_project(name, file)

    def _find_project_packages(self, where: Path | None = None) -> list[str]:
        if where in self._project_packages_cache:
            return self._project_packages_cache[where]

        resolved = where
        if resolved is None:
            try:
                resolved = here()
            except RuntimeError:
                self._project_packages_cache[None] = []
                return []

        src = resolved / "src"
        if src.is_dir():
            result = self._find_project_packages(src)
            self._project_packages_cache[where] = result
            return result

        _NON_SOURCE_DIRS = {"tests", "test", "build", "dist", "vendor", "examples"}
        result = [
            path.name
            for path in resolved.iterdir()
            if (
                path.is_dir()
                and path.name not in _NON_SOURCE_DIRS
                and not path.name.startswith(".")
                and not path.name.startswith("test")
                and (path / "__init__.py").exists()
            )
        ]
        self._project_packages_cache[where] = result
        return result

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
        except Exception:
            pkg_dist = {}

        dist_to_imports: dict[str, list[str]] = defaultdict(list)
        for pkg_name, dists in pkg_dist.items():
            for d in dists:
                key = d.lower().replace("-", "_")
                if pkg_name not in dist_to_imports[key]:
                    dist_to_imports[key].append(pkg_name)

        result: list[str] = []
        for d in dist_names:
            key = d.lower().replace("-", "_")
            if key in dist_to_imports:
                result.extend(dist_to_imports[key])
            elif key in _DIST_FALLBACK_MAP:
                result.append(_DIST_FALLBACK_MAP[key])
            else:
                result.append(key)
        return result

    def _find_package_in_our_project(self, name: str, file: Path) -> str | None:
        if name not in self.import_cache:
            self.index_packages([name])

        candidates = self.import_cache[name]
        if not candidates:
            return None
        elif len(candidates) == 1:
            return list(candidates)[0][0]

        usage, method_calls = analyze_usage(file, name)

        if not usage and not method_calls:
            return statistics.mode([line for line, _ in candidates])

        attr_filtered = [
            (line, def_file)
            for line, def_file in candidates
            if not usage or all(attr in parse_class_attributes(def_file, name) for attr in usage)
        ]

        if not attr_filtered:
            return statistics.mode([line for line, _ in candidates])

        if len(attr_filtered) == 1 or not method_calls:
            return statistics.mode([line for line, _ in attr_filtered])

        sig_filtered = [
            line
            for line, def_file in attr_filtered
            if call_signatures_match(method_calls, parse_method_signatures(def_file, name))
        ]

        if sig_filtered:
            return statistics.mode(sig_filtered)

        return statistics.mode([line for line, _ in attr_filtered])

    @staticmethod
    @cache
    def _find_package_in_modules(name: str) -> str | None:
        try:
            package_specs = importlib.util.find_spec(name)
        except (ImportError, ValueError):
            return None
        if package_specs is None:
            return None
        return f"import {name}"

    def _find_package_in_libraries(self, name: str) -> str | None:
        if name in self._libraries_cache:
            return self._libraries_cache[name]

        result = None
        for lib in common_libraries:
            objects = self.extract_package_objects(lib)
            if name in objects:
                result = objects[name][0]
                break

        self._libraries_cache[name] = result
        return result

    def _get_additional_statements(self) -> dict[str, str] | None:
        config_statements = self.config.get("common_statements")
        if config_statements:
            return config_statements
        return self.config.get("tool", {}).get("autoimport", {}).get("common_statements")

    def _find_package_in_common_statements(self, name: str) -> str | None:
        if name in self._common_stmt_cache:
            return self._common_stmt_cache[name]

        local_common_statements = common_statements.copy()
        additional_statements = self._get_additional_statements()
        if additional_statements:
            local_common_statements.update(additional_statements)

        result = local_common_statements.get(name)
        self._common_stmt_cache[name] = result
        return result

    def get_cache_path(self, package_name: str) -> Path:
        hash_name = hashlib.sha256(package_name.encode()).hexdigest()
        return self.cache_dir / f"{hash_name}.pkl"

    _SKIP_SUBDIRS = {"__pycache__", "tests", "test", ".mypy_cache", ".ruff_cache"}

    def _iter_package_files(self, package_name: str) -> dict[str, tuple[Path, bool]]:
        parts = package_name.split(".")
        for path_entry in sys.path:
            candidate = Path(path_entry, *parts)
            if candidate.is_dir():
                result: dict[str, tuple[Path, bool]] = {}
                for py_file in candidate.rglob("*.py"):
                    rel = py_file.relative_to(candidate)
                    if any(
                        part in self._SKIP_SUBDIRS or part.startswith(".")
                        for part in rel.parts[:-1]
                    ):
                        continue
                    rel_parts = list(py_file.relative_to(Path(path_entry)).with_suffix("").parts)
                    is_init = rel_parts[-1] == "__init__"
                    if is_init:
                        mod_name = ".".join(rel_parts[:-1])
                    else:
                        if rel_parts[-1].startswith("_"):
                            continue
                        mod_name = ".".join(rel_parts)
                    result[mod_name] = (py_file, is_init)
                return result

            single = candidate.with_suffix(".py")
            if single.is_file():
                return {package_name: (single, False)}

        return {}

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
                module_defs[mod_name], module_reexports[mod_name] = parse_module_definitions(
                    file_path, mod_name
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

        # Surface names re-exported from private C-extension modules (e.g.
        # `typing.py` does `from _typing import Union`, so Union should resolve
        # to `from typing import Union`). Restricted to underscore-prefixed
        # sources, which are the convention for private C-extension backends —
        # public stdlib C extensions like `itertools` are reachable directly
        # and should not be funneled through the re-exporting module.
        for mod_name, reexports in module_reexports.items():
            file_path = all_files[mod_name][0]
            for name, source in reexports.items():
                if source in all_files or name in objects:
                    continue
                top_level = source.split(".", 1)[0]
                if not top_level.startswith("_"):
                    continue
                objects.setdefault(name, []).append(f"from {mod_name} import {name}")
                definition_files.setdefault(name, []).append(file_path)

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        tmp_cache = cache_path.with_suffix(".pkl.tmp")
        try:
            tmp_cache.write_bytes(
                pickle.dumps(
                    {
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
                    }
                )
            )
            tmp_cache.replace(cache_path)
        except Exception:
            tmp_cache.unlink(missing_ok=True)

        return objects, definition_files
