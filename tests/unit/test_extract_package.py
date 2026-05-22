"""Test the extraction of package objects."""

import os
import sys
from pathlib import Path

import pytest

from autoimport.model import SourceCode


def extract_package_objects(arg: str) -> dict[str, list[str]]:
    return SourceCode("").extract_package_objects(arg)


@pytest.fixture
def package(tmp_path: Path):
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


def test_extraction_returns_package_functions(package: Path) -> None:
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


def test_extraction_works_when_module_raises_on_import(package: Path) -> None:
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


def test_extraction_promotes_to_highest_init_reexport(package: Path) -> None:
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


def test_extraction_partial_promotion_stops_at_broken_chain(package: Path) -> None:
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
