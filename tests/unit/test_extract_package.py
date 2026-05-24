"""Test the extraction of package objects."""

import os
import shutil
import sys
from pathlib import Path

import pytest

from autoimport.model import PackageFinder


def extract_package_objects(package_name: str) -> dict[str, list[str]]:
    """Return importable names from a package."""
    return PackageFinder().extract_package_objects(package_name)


@pytest.fixture
def package(tmp_path: Path):
    (tmp_path / "pyproject.toml").touch()

    package = tmp_path / "package"
    package.mkdir()
    (package / "__init__.py").touch()

    sub = package / "sub"
    sub.mkdir()
    (sub / "__init__.py").touch()

    cwd = os.getcwd()
    sys.path.append(str(tmp_path))

    try:
        os.chdir(tmp_path)
        yield package
    finally:
        os.chdir(cwd)
        sys.path.remove(str(tmp_path))
        shutil.rmtree(".autoimport_cache", ignore_errors=True)


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
    # package/__init__.py does NOT re-export MidClass

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


def test_extraction_syntax_error_in_module_is_skipped(package: Path):
    """
    Given: A module with a syntax error alongside valid modules.
    When: extract package objects is called.
    Then: The broken file is skipped and valid definitions are still returned.
    """
    (package / "broken.py").write_text("def (: pass")  # invalid syntax
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
    file_c.write_text("w = Widget()\nw.fly()\n")  # .fly() is in neither class

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


# @pytest.mark.xfail(reason="import copy not implemented")
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
        "class Config(Base):\n def __init__(self):\n  super().__init__()\n  self.timeout: int = 30\n"
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


def test_parse_class_attributes_returns_empty_set_when_class_not_found(package: Path):
    """
    Given: A file that does not contain the requested class.
    When: _parse_class_attributes is called.
    Then: An empty set is returned.
    """
    f = package / "mod.py"
    f.write_text("class Other:\n pass\n")

    result = PackageFinder._parse_class_attributes(f, "Missing")
    assert result == set()


def test_parse_class_attributes_handles_syntax_error(package: Path):
    """
    Given: A file with a syntax error.
    When: _parse_class_attributes is called.
    Then: An empty set is returned without raising.
    """
    f = package / "bad.py"
    f.write_text("class (: pass")

    result = PackageFinder._parse_class_attributes(f, "Anything")

    assert result == set()


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

    # Inner is still found at its defining module; broken __init__ means no promotion
    assert result == {"Inner": ["from package.sub.inner import Inner"]}


def test_extraction_uses_cache_on_second_call(package: Path):
    """
    Given: extract_package_objects has been called once (cache written).
    When: The same package is extracted again without any file changes.
    Then: The result is identical (cache hit path exercised).
    """
    (package / "cached.py").write_text("def cached_func(): pass")

    first = extract_package_objects("package")
    second = extract_package_objects("package")

    assert first == second
    assert "cached_func" in second
