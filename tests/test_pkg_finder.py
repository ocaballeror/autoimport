"""Tests for PackageFinder import resolution logic."""

from pathlib import Path
from unittest.mock import patch

import pytest

from autoimport.finder import PackageFinder


def extract_package_objects(package_name: str) -> dict[str, list[str]]:
    """Return importable names from a package."""
    return PackageFinder().extract_package_objects(package_name)


def test_extraction_returns_package_functions(package: Path):
    """
    Given: A package with functions.
    When: extract package objects is called
    Then: All the functions are extracted
    """
    outer = package / "outer.py"
    outer.write_text("def foo():\n pass")

    inner = package / "sub" / "inner.py"
    inner.write_text("def bar():\n pass")

    result = extract_package_objects("package")

    assert result == {
        "foo": ["from package.outer import foo"],
        "bar": ["from package.sub.inner import bar"],
    }


def test_extraction_returns_package_classes(package: Path):
    """
    Given: A package with classes.
    When: extract package objects is called.
    Then: All the classes are extracted.
    """
    outer = package / "outer.py"
    outer.write_text("class Foo:\n pass")

    inner = package / "sub" / "inner.py"
    inner.write_text("class Bar:\n pass")

    result = extract_package_objects("package")

    assert result == {
        "Foo": ["from package.outer import Foo"],
        "Bar": ["from package.sub.inner import Bar"],
    }


def test_extraction_returns_package_variables(package: Path):
    """
    Given: A package with top level variables.
    When: extract package objects is called.
    Then: All variables are extracted.
    """
    outer = package / "outer.py"
    outer.write_text("foo = 1")

    inner = package / "sub" / "inner.py"
    inner.write_text("bar = 'bar'")

    result = extract_package_objects("package")

    assert result == {
        "foo": ["from package.outer import foo"],
        "bar": ["from package.sub.inner import bar"],
    }


def test_extraction_returns_empty_dict_if_package_is_not_importable():
    """
    Given: Autoimport can't import the package.
    When: the extract package objects is called.
    Then: An empty directory is returned
    """
    result = extract_package_objects("inexistent")

    assert not result


def test_extraction_works_when_module_raises_on_import(package: Path):
    """
    Given: A module that would raise an error if imported.
    When: extract package objects is called.
    Then: Top-level definitions are still extracted (AST, not import).
    """
    broken = package / "broken.py"
    broken.write_text("raise RuntimeError('do not import me')\n\ndef safe_func():\n pass")

    result = extract_package_objects("package")

    assert "safe_func" in result
    assert result["safe_func"] == ["from package.broken import safe_func"]


def test_extraction_promotes_to_highest_init_reexport(package: Path):
    """
    Given: A name defined in a leaf module, re-exported up two __init__.py levels.
    When: extract package objects is called.
    Then: The import line points to the top-level package.
    """
    inner = package / "sub" / "inner.py"
    inner.write_text("class DeepClass:\n pass")

    (package / "sub" / "__init__.py").write_text("from .inner import DeepClass")
    (package / "__init__.py").write_text("from .sub import DeepClass")

    result = extract_package_objects("package")

    assert result == {"DeepClass": ["from package import DeepClass"]}


def test_extraction_partial_promotion_stops_at_broken_chain(package: Path):
    """
    Given: A name re-exported from sub/__init__ but NOT from package/__init__.
    When: extract package objects is called.
    Then: The import line points to the sub-package, not the top-level package.
    """
    inner = package / "sub" / "inner.py"
    inner.write_text("class MidClass:\n pass")

    (package / "sub" / "__init__.py").write_text("from .inner import MidClass")

    result = extract_package_objects("package")

    assert result == {"MidClass": ["from package.sub import MidClass"]}


def test_extraction_excludes_private_names(package: Path):
    """
    Given: A module with private functions, classes and variables.
    When: extract package objects is called.
    Then: Names starting with _ are not included.
    """
    (package / "things.py").write_text(
        "def _private(): pass\nclass _PrivateClass: pass\n_private_var = 1\ndef public(): pass\n"
    )

    result = extract_package_objects("package")

    assert list(result.keys()) == ["public"]


def test_extraction_includes_annotated_variables(package: Path):
    """
    Given: A module with annotated variable declarations.
    When: extract package objects is called.
    Then: Both with and without values are extracted.
    """
    (package / "things.py").write_text("x: int = 1\ny: str\n")

    result = extract_package_objects("package")

    assert "x" in result
    assert "y" in result


def test_extraction_same_name_in_two_files_returns_both_candidates(package: Path):
    """
    Given: Two modules that each define a class with the same name.
    When: extract package objects is called.
    Then: Both import lines are returned as candidates.
    """
    (package / "a.py").write_text("class Duplicate:\n pass")
    (package / "b.py").write_text("class Duplicate:\n pass")

    result = extract_package_objects("package")

    assert len(result["Duplicate"]) == 2
    assert "from package.a import Duplicate" in result["Duplicate"]
    assert "from package.b import Duplicate" in result["Duplicate"]


def test_extraction_star_import_in_init_does_not_promote(package: Path):
    """
    Given: __init__.py uses 'from .inner import *'.
    When: extract package objects is called.
    Then: Names from inner are still reported at their defining module, not promoted.
    """
    (package / "sub" / "inner.py").write_text("class StarClass:\n pass")
    (package / "sub" / "__init__.py").write_text("from .inner import *")

    result = extract_package_objects("package")

    assert result == {"StarClass": ["from package.sub.inner import StarClass"]}


def test_extraction_aliased_reexport_does_not_promote_original(package: Path):
    """
    Given: __init__.py re-exports a name under an alias ('from .inner import Foo as Bar').
    When: extract package objects is called.
    Then: Foo is NOT promoted (we don't track aliased re-exports).
    """
    (package / "sub" / "inner.py").write_text("class Foo:\n pass")
    (package / "sub" / "__init__.py").write_text("from .inner import Foo as Bar")

    result = extract_package_objects("package")

    assert result == {"Foo": ["from package.sub.inner import Foo"]}


def test_extraction_picks_up_names_listed_in___all__(package: Path):
    """
    Given: A module that lists names in __all__ but does not define them as
           top-level statements.
    When: extract package objects is called.
    Then: The names listed in __all__ are still exposed as importable.
    """
    (package / "things.py").write_text(
        "from .other import inner_thing\n__all__ = ['inner_thing', 'StringOnly']\n"
    )

    result = extract_package_objects("package")

    assert "inner_thing" in result
    assert "StringOnly" in result
    assert "from package.things import StringOnly" in result["StringOnly"]


def test_extraction_picks_up_conditional_imports(package: Path):
    """
    Given: A module that defines names inside `try/except ImportError` and
           `if sys.version_info:` blocks at the top level.
    When: extract package objects is called.
    Then: Those names are still collected as exports.
    """
    (package / "compat.py").write_text(
        "import sys\n"
        "if sys.version_info >= (3, 11):\n"
        "    def new_feature():\n"
        "        pass\n"
        "else:\n"
        "    def legacy_feature():\n"
        "        pass\n"
        "try:\n"
        "    class FastImpl:\n"
        "        pass\n"
        "except ImportError:\n"
        "    class FastImpl:  # noqa\n"
        "        pass\n"
    )

    result = extract_package_objects("package")

    assert "new_feature" in result
    assert "legacy_feature" in result
    assert "FastImpl" in result


def test_extraction_syntax_error_in_module_is_skipped(package: Path):
    """
    Given: A module with a syntax error alongside valid modules.
    When: extract package objects is called.
    Then: The broken file is skipped and valid definitions are still returned.
    """
    (package / "broken.py").write_text("def (: pass")
    (package / "valid.py").write_text("def good_func(): pass")

    result = extract_package_objects("package")

    assert "good_func" in result
    assert "broken" not in str(result)


def test_find_in_ours_no_usage_returns_mode(package: Path):
    """
    Given: Multiple candidates but the source code gives no usage clues.
    When: _find_package_in_our_project is called.
    Then: The most common candidate (mode) is returned.
    """
    file_a = package / "a.py"
    file_a.write_text("class Thing:\n pass")
    file_b = package / "b.py"
    file_b.write_text("class Thing:\n pass")

    file_c = Path("c.py")
    file_c.write_text("x = Thing")

    sc = PackageFinder()

    result = sc._find_package_in_our_project("Thing", file_c)
    assert result == "from package.a import Thing" or result == "from package.b import Thing"


def test_find_in_ours_selects_by_method_match(package: Path):
    """
    Given: Two classes with the same name; source calls methods only one of them has.
    When: _find_package_in_our_project is called.
    Then: The candidate whose class declares the used methods is selected.
    """
    file_a = package / "a.py"
    file_a.write_text("class Conn:\n def connect(self): pass\n def query(self): pass\n")
    file_b = package / "b.py"
    file_b.write_text("class Conn:\n def save(self): pass\n def load(self): pass\n")

    file_c = Path("c.py")
    file_c.write_text("db = Conn()\ndb.connect()\ndb.query()\n")

    sc = PackageFinder()
    result = sc._find_package_in_our_project("Conn", file_c)

    assert result == "from package.a import Conn"


def test_find_in_ours_falls_back_to_mode_when_no_match(package: Path):
    """
    Given: Source uses methods that no candidate's class declares.
    When: _find_package_in_our_project is called.
    Then: Falls back to the most common candidate.
    """
    file_a = package / "a.py"
    file_a.write_text("class Widget:\n def draw(self): pass\n")
    file_b = package / "b.py"
    file_b.write_text("class Widget:\n def draw(self): pass\n")

    file_c = Path("c.py")
    file_c.write_text("w = Widget()\nw.fly()\n")

    sc = PackageFinder()

    result = sc._find_package_in_our_project("Widget", file_c)
    assert result == "from package.a import Widget" or result == "from package.b import Widget"


def test_find_in_ours_matches_self_instance_attributes(package: Path):
    """
    Given: Class sets instance attributes via self.x = ... in __init__.
    When: Source uses those attributes and _find_package_in_our_project is called.
    Then: The candidate with matching instance attributes is chosen.
    """
    file_a = package / "a.py"
    file_a.write_text(
        "class Config:\n def __init__(self):\n  self.host = 'localhost'\n  self.port = 5432\n"
    )
    file_b = package / "b.py"
    file_b.write_text(
        "class Config:\n"
        " def __init__(self):\n"
        "  self.username = 'admin'\n"
        "  self.password = 'secret'\n"
    )

    file_c = Path("c.py")
    file_c.write_text("cfg = Config()\ncfg.host\ncfg.port\n")

    sc = PackageFinder()
    result = sc._find_package_in_our_project("Config", file_c)
    assert result == "from package.a import Config"


def test_find_in_ours_matches_annotated_class_attributes(package: Path):
    """
    Given: Class declares attributes via annotation (x: int).
    When: Source uses those attributes and _find_package_in_our_project is called.
    Then: The candidate with matching annotated attributes is chosen.
    """
    file_a = package / "a.py"
    file_a.write_text("class Record:\n name: str\n value: int\n")
    file_b = package / "b.py"
    file_b.write_text("class Record:\n title: str\n count: int\n")

    file_c = Path("c.py")
    file_c.write_text("r = Record()\nr.name\nr.value\n")

    sc = PackageFinder()
    result = sc._find_package_in_our_project("Record", file_c)
    assert result == "from package.a import Record"


def test_find_in_ours_matches_annotated_self_assignments(package: Path):
    """
    Given: Class sets instance attributes via annotated assignments (self.x: int = ...).
    When: Source uses those attributes and _find_package_in_our_project is called.
    Then: The candidate with matching attributes is chosen.
    """
    file_a = package / "a.py"
    file_a.write_text(
        "class Repo:\n def __init__(self):\n  self.url: str = ''\n  self.branch: str = 'main'\n"
    )
    file_b = package / "b.py"
    file_b.write_text(
        "class Repo:\n def __init__(self):\n  self.name: str = ''\n  self.owner: str = ''\n"
    )

    file_c = Path("c.py")
    file_c.write_text("r = Repo()\nr.url\nr.branch\n")

    sc = PackageFinder()
    result = sc._find_package_in_our_project("Repo", file_c)
    assert result == "from package.a import Repo"


@pytest.mark.xfail(reason="import copy not implemented")
def test_find_in_ours_copies_unknown_from_other(package: Path):
    file_a = package / "a.py"
    file_a.write_text("from requests.session import Session")

    file_b = package / "b.py"
    file_b.write_text("Session()")

    sc = PackageFinder()
    result = sc._find_package_in_our_project("Session", file_b)
    assert result == "from requests import Session"


def test_find_in_ours_matches_inherited_attributes(package: Path):
    """
    Given: A subclass inherits attributes from a base class defined in the same file.
    When: Source uses those inherited attributes and _find_package_in_our_project is called.
    Then: The candidate whose inherited attributes match is chosen.
    """
    file_a = package / "a.py"
    file_a.write_text(
        "class Base:\n def __init__(self):\n  self.host: str = ''\n  self.port: int = 0\n"
        "class Config(Base):\n def __init__(self):\n  super().__init__()\n  self.timeout: int = 30"
    )
    file_b = package / "b.py"
    file_b.write_text(
        "class Config:\n def __init__(self):\n  self.name: str = ''\n  self.value: int = 0\n"
    )

    file_c = Path("c.py")
    file_c.write_text("c = Config()\nc.host\nc.port\n")

    sc = PackageFinder()
    result = sc._find_package_in_our_project("Config", file_c)
    assert result == "from package.a import Config"


def test_find_in_ours_disambiguates_by_method_arg_type_via_assignment(package: Path):
    """
    Given: Two classes share a method name but differ in parameter types.
    When: The source assigns T() to a variable then calls the method with a typed literal.
    Then: The candidate whose parameter type matches the literal is chosen.
    """
    file_a = package / "a.py"
    file_a.write_text("class T:\n def meth(self, a: str): pass\n")
    file_b = package / "b.py"
    file_b.write_text("class T:\n def meth(self, a: int): pass\n")

    file_c = Path("c.py")
    file_c.write_text("t = T()\nt.meth(1)\n")

    sc = PackageFinder()
    result = sc._find_package_in_our_project("T", file_c)
    assert result == "from package.b import T"


def test_find_in_ours_disambiguates_by_method_arg_type_direct_call(package: Path):
    """
    Given: Two classes share a method name but differ in parameter types.
    When: The source calls the method directly on a T() expression (no assignment).
    Then: The candidate whose parameter type matches the literal is chosen.
    """
    file_a = package / "a.py"
    file_a.write_text("class T:\n def meth(self, a: str): pass\n")
    file_b = package / "b.py"
    file_b.write_text("class T:\n def meth(self, a: int): pass\n")

    file_c = Path("c.py")
    file_c.write_text("T().meth(1)\n")

    sc = PackageFinder()
    result = sc._find_package_in_our_project("T", file_c)
    assert result == "from package.b import T"


def test_find_in_ours_disambiguates_by_arg_count(package: Path):
    """
    Given: Two classes share a method name but differ in the number of parameters.
    When: The source calls the method with a number of arguments that only one candidate accepts.
    Then: The candidate with the matching arity is chosen.
    """
    file_a = package / "a.py"
    file_a.write_text("class T:\n def meth(self, a, b): pass\n")
    file_b = package / "b.py"
    file_b.write_text("class T:\n def meth(self, a): pass\n")

    file_c = Path("c.py")
    file_c.write_text("T().meth(1, 2)\n")

    sc = PackageFinder()
    result = sc._find_package_in_our_project("T", file_c)
    assert result == "from package.a import T"


def test_find_in_ours_disambiguates_by_keyword_arg_name(package: Path):
    """
    Given: Two classes share a method name but use different parameter names.
    When: The source calls the method with a keyword argument that only one candidate has.
    Then: The candidate whose parameter name matches is chosen.
    """
    file_a = package / "a.py"
    file_a.write_text("class T:\n def meth(self, a=None, b=None): pass\n")
    file_b = package / "b.py"
    file_b.write_text("class T:\n def meth(self, x=None, y=None): pass\n")

    file_c = Path("c.py")
    file_c.write_text("T().meth(b=1)\n")

    sc = PackageFinder()
    result = sc._find_package_in_our_project("T", file_c)
    assert result == "from package.a import T"


def test_extraction_type_alias_is_included(package: Path):
    """
    Given: A module with a type alias (PEP 695 syntax).
    When: extract package objects is called.
    Then: The alias name is included in results.
    """
    (package / "types.py").write_text("type Vector = list[float]\n")

    result = extract_package_objects("package")

    assert "Vector" in result
    assert result["Vector"] == ["from package.types import Vector"]


def test_extraction_syntax_error_in_init_is_skipped(package: Path):
    """
    Given: A sub-package whose __init__.py has a syntax error.
    When: extract package objects is called.
    Then: The sub-package's __init__ re-exports are treated as empty (no crash).
    """
    (package / "sub" / "inner.py").write_text("class Inner:\n pass")
    (package / "sub" / "__init__.py").write_text("from (bad syntax")

    result = extract_package_objects("package")

    assert result == {"Inner": ["from package.sub.inner import Inner"]}


def test_find_in_ours_falls_back_to_attr_mode_when_no_sig_matches(package: Path):
    """
    Given: Multiple candidates share the same method but the call passes more args than
           any candidate accepts, so sig_filtered ends up empty.
    When: _find_package_in_our_project is called.
    Then: Falls back to mode of the attr-filtered candidates rather than returning None.
    """
    (package / "a.py").write_text("class T:\n def meth(self, a): pass\n")
    (package / "b.py").write_text("class T:\n def meth(self, a): pass\n")
    file_c = Path("c.py")
    file_c.write_text("T().meth(1, 2, 3)\n")

    sc = PackageFinder()
    result = sc._find_package_in_our_project("T", file_c)

    assert result is not None


def test_find_package_in_libraries_resolves_typing_names():
    """
    Given: Names from the typing module, including ones backed by a C extension.
    When: _find_package_in_libraries is called.
    Then: All names resolve to `from typing import X`, not to the C extension.
    """
    finder = PackageFinder()
    assert finder._find_package_in_libraries("Optional") == "from typing import Optional"
    assert finder._find_package_in_libraries("Any") == "from typing import Any"
    assert finder._find_package_in_libraries("Union") == "from typing import Union"
    assert finder._find_package_in_libraries("TypeVar") == "from typing import TypeVar"


def test_find_package_in_libraries_resolves_stdlib_names():
    """
    Given: Names from extended stdlib libraries in common_libraries.
    When: _find_package_in_libraries is called.
    Then: They resolve to imports from the appropriate stdlib module.
    """
    finder = PackageFinder()
    assert finder._find_package_in_libraries("reduce") == "from functools import reduce"
    assert finder._find_package_in_libraries("wraps") == "from functools import wraps"
    assert finder._find_package_in_libraries("dataclass") == "from dataclasses import dataclass"
    assert finder._find_package_in_libraries("field") == "from dataclasses import field"
    assert finder._find_package_in_libraries("contextmanager") == (
        "from contextlib import contextmanager"
    )


def test_find_project_packages_returns_empty_when_no_project_root():
    """
    Given: No pyproject.toml (or other marker) is found above the cwd.
    When: _find_project_packages is called.
    Then: Returns [] rather than raising.
    """
    with patch("autoimport.finder.here", side_effect=RuntimeError("no project")):
        sc = PackageFinder()
        result = sc._find_project_packages()
    assert result == []


def test_read_project_dependencies_returns_dep_names(tmp_path):
    """
    Given: A pyproject.toml with a dependencies list.
    When: _read_project_dependencies is called.
    Then: The dependency names are returned, stripped of version specifiers.
    """
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[project]\ndependencies = ["requests>=2.0", "click[extra]>=7", "my-package"]\n'
    )
    result = PackageFinder._read_project_dependencies(tmp_path)
    assert len(result) == 3
    assert result[2] == "my_package"
    assert all(r for r in result)


def test_read_project_dependencies_handles_markers(tmp_path):
    """
    Given: A dependency with a PEP 508 environment marker.
    When: _read_project_dependencies is called.
    Then: The name before the marker is returned.
    """
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text("[project]\ndependencies = [\"some-lib; python_version>'3.8'\"]\n")
    result = PackageFinder._read_project_dependencies(tmp_path)
    assert result == ["some_lib"]


def test_read_project_dependencies_missing_pyproject(tmp_path):
    """
    Given: A directory without a pyproject.toml.
    When: _read_project_dependencies is called.
    Then: An empty list is returned without raising.
    """
    result = PackageFinder._read_project_dependencies(tmp_path)
    assert result == []


def test_read_project_dependencies_invalid_toml(tmp_path):
    """
    Given: A pyproject.toml with invalid TOML syntax.
    When: _read_project_dependencies is called.
    Then: An empty list is returned without raising.
    """
    (tmp_path / "pyproject.toml").write_text("this is not [valid toml !!!")
    result = PackageFinder._read_project_dependencies(tmp_path)
    assert result == []


def test_read_project_dependencies_no_project_section(tmp_path):
    """
    Given: A pyproject.toml with no [project] table.
    When: _read_project_dependencies is called.
    Then: An empty list is returned.
    """
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\nline-length = 88\n")
    result = PackageFinder._read_project_dependencies(tmp_path)
    assert result == []


def test_index_packages_includes_dependency_exports(package: Path):
    """
    Given: A pyproject.toml that lists a dependency, and that dependency has a package
           directory available on sys.path.
    When: index_packages is called.
    Then: Objects exported by the dependency appear in import_cache alongside project objects.
    """
    pyproject = package.parent / "pyproject.toml"
    pyproject.write_text('[project]\ndependencies = ["depkg"]\n')

    dep = package.parent / "depkg"
    dep.mkdir()
    (dep / "__init__.py").touch()
    (dep / "api.py").write_text("class DepClass:\n pass\n")

    (package / "mod.py").write_text("class DepClass:\n def dep_method(self): pass\n")

    sc = PackageFinder()
    sc.index_packages(["DepClass"])

    assert len(sc.import_cache["DepClass"]) == 2
    import_lines = {line for line, _ in sc.import_cache["DepClass"]}
    assert "from package.mod import DepClass" in import_lines
    assert "from depkg.api import DepClass" in import_lines


def test_read_project_dependencies_uses_fallback_map_for_known_dists(tmp_path):
    """
    Given: A dist whose import name cannot be derived by the lowercase/underscore
           heuristic (e.g. pyyaml → yaml) and is not installed locally.
    When: _read_project_dependencies is called.
    Then: The static fallback map is consulted to resolve the import name.
    """
    (tmp_path / "pyproject.toml").write_text(
        '[project]\ndependencies = ["pyyaml", "beautifulsoup4", "python-dateutil"]\n'
    )
    with patch("autoimport.finder.importlib.metadata.packages_distributions", return_value={}):
        result = PackageFinder._read_project_dependencies(tmp_path)
    assert "yaml" in result
    assert "bs4" in result
    assert "dateutil" in result


def test_read_project_dependencies_falls_back_when_metadata_raises(tmp_path):
    """
    Given: importlib.metadata.packages_distributions() raises unexpectedly.
    When: _read_project_dependencies is called.
    Then: Names are returned using simple lowercasing/normalization, not an empty list.
    """
    (tmp_path / "pyproject.toml").write_text('[project]\ndependencies = ["my-lib"]\n')
    with patch(
        "autoimport.finder.importlib.metadata.packages_distributions", side_effect=RuntimeError
    ):
        result = PackageFinder._read_project_dependencies(tmp_path)
    assert result == ["my_lib"]


def test_index_packages_includes_stdlib_library_candidates():
    """
    Given: A name exported by a standard library in common_libraries.
    When: index_packages is called.
    Then: A candidate for that name appears in import_cache.
    """
    finder = PackageFinder()
    finder.index_packages(["reduce"])
    import_lines = {line for line, _ in finder.import_cache["reduce"]}
    assert "from functools import reduce" in import_lines


def test_index_packages_includes_stdlib_module_candidates():
    """
    Given: A name that is itself an importable stdlib module.
    When: index_packages is called.
    Then: An 'import {name}' candidate appears in import_cache.
    """
    finder = PackageFinder()
    finder.index_packages(["os"])
    import_lines = {line for line, _ in finder.import_cache["os"]}
    assert "import os" in import_lines


def test_find_package_resolves_stdlib_library_name_without_short_circuit(tmp_path):
    """
    Given: A name from a common stdlib library and no project package present.
    When: find_package is called.
    Then: The correct stdlib import is returned via the candidate path (not a short-circuit).
    """
    finder = PackageFinder()
    usage_file = tmp_path / "usage.py"
    usage_file.write_text("x = wraps\n")
    result = finder.find_package("wraps", usage_file)
    assert result == "from functools import wraps"


def test_find_package_resolves_stdlib_module_import(tmp_path):
    """
    Given: A name that is an importable stdlib module.
    When: find_package is called without a project present.
    Then: 'import {name}' is returned.
    """
    finder = PackageFinder()
    usage_file = tmp_path / "usage.py"
    usage_file.write_text("os.getcwd()\n")
    result = finder.find_package("os", usage_file)
    assert result == "import os"


def test_find_package_prefers_project_class_over_stdlib_when_attrs_match(package: Path):
    """
    Given: A project class and a same-named stdlib function exist as candidates.
    When: The source uses instance attributes that only the project class has.
    Then: The project class is chosen over the stdlib candidate.
    """
    (package / "things.py").write_text(
        "class reduce:\n def my_method(self): pass\n def other_method(self): pass\n"
    )
    usage_file = package.parent / "usage.py"
    usage_file.write_text("x = reduce()\nx.my_method()\n")

    finder = PackageFinder()
    result = finder.find_package("reduce", usage_file)
    assert result == "from package.things import reduce"


def test_find_package_prefers_stdlib_when_project_class_attrs_do_not_match(package: Path):
    """
    Given: A project class and a same-named stdlib name exist as candidates.
    When: The source uses no attribute accesses that match the project class.
    Then: The stdlib candidate is included in the result (mode selection, not dropped entirely).
    """
    (package / "things.py").write_text("class reduce:\n def project_only(self): pass\n")
    usage_file = package.parent / "usage.py"
    # No instance attribute access — analyze_usage returns empty usage
    usage_file.write_text("result = reduce(range(10), lambda a, b: a + b)\n")

    finder = PackageFinder()
    result = finder.find_package("reduce", usage_file)
    # Both are candidates with no discriminating info; result must be one of them
    assert result in ("from functools import reduce", "from package.things import reduce")


def test_find_package_stdlib_module_filtered_out_when_project_class_matches(package: Path):
    """
    Given: A project class shares its name with a stdlib module name.
    When: Source code uses instance attributes from the project class.
    Then: The 'import {name}' stdlib candidate is filtered out in favour of the project class.
    """
    (package / "json_wrapper.py").write_text("class json:\n def load_data(self): pass\n")
    usage_file = package.parent / "usage.py"
    usage_file.write_text("x = json()\nx.load_data()\n")

    finder = PackageFinder()
    result = finder.find_package("json", usage_file)
    assert result == "from package.json_wrapper import json"
