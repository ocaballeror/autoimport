"""Tests for AST parsing utilities in ast_utils.py."""

from autoimport.ast_utils import (
    _CallInfo,
    _MethodSig,
    analyze_usage,
    call_signatures_match,
    parse_class_attributes,
    parse_method_signatures,
    parse_module_definitions,
)


def test_analyze_usage_returns_empty_on_syntax_error(tmp_path):
    """
    Given: The target file contains a syntax error.
    When: analyze_usage is called.
    Then: Returns ([], {}) without raising.
    """
    f = tmp_path / "bad.py"
    f.write_text("def (: broken")
    uses, calls = analyze_usage(f, "Foo")
    assert uses == []
    assert calls == {}


def test_infer_arg_type_handles_collection_literals_and_unknown(tmp_path):
    """
    Given: A call site that passes list, dict, set, tuple literals and an unknown variable.
    When: analyze_usage is called.
    Then: Each literal is inferred correctly; the unknown variable produces None.
    """
    f = tmp_path / "mod.py"
    f.write_text("T().meth([], {}, {1, 2}, (), unknown_var)\n")

    _, calls = analyze_usage(f, "T")

    assert calls["meth"].positional_types == ["list", "dict", "set", "tuple", None]


def test_parse_class_attributes_returns_empty_set_when_class_not_found(tmp_path):
    """
    Given: A file that does not contain the requested class.
    When: parse_class_attributes is called.
    Then: An empty set is returned.
    """
    f = tmp_path / "mod.py"
    f.write_text("class Other:\n pass\n")

    result = parse_class_attributes(f, "Missing")
    assert result == set()


def test_parse_class_attributes_handles_syntax_error(tmp_path):
    """
    Given: A file with a syntax error.
    When: parse_class_attributes is called.
    Then: An empty set is returned without raising.
    """
    f = tmp_path / "bad.py"
    f.write_text("class (: pass")

    result = parse_class_attributes(f, "Anything")

    assert result == set()


def test_parse_class_attributes_includes_plain_class_assignments(tmp_path):
    """
    Given: A class with plain (non-annotated) class-level assignments.
    When: parse_class_attributes is called.
    Then: Those assignment targets are included in the returned attribute set.
    """
    f = tmp_path / "mod.py"
    f.write_text("class T:\n    x = 5\n    y = 'hello'\n")

    result = parse_class_attributes(f, "T")

    assert "x" in result
    assert "y" in result


def test_parse_method_signatures_returns_empty_on_syntax_error(tmp_path):
    """
    Given: The source file contains a syntax error.
    When: parse_method_signatures is called.
    Then: Returns {} without raising.
    """
    f = tmp_path / "bad.py"
    f.write_text("def (: broken")

    assert parse_method_signatures(f, "T") == {}


def test_parse_method_signatures_returns_empty_when_class_not_found(tmp_path):
    """
    Given: A file that does not define the requested class.
    When: parse_method_signatures is called.
    Then: Returns {}.
    """
    f = tmp_path / "mod.py"
    f.write_text("class Other:\n def meth(self): pass\n")

    assert parse_method_signatures(f, "Missing") == {}


def test_parse_method_signatures_includes_inherited_methods(tmp_path):
    """
    Given: A class that inherits from a base class defined in the same file.
    When: parse_method_signatures is called on the subclass.
    Then: Methods from the base class are included alongside the subclass's own methods.
    """
    f = tmp_path / "mod.py"
    f.write_text(
        "class Base:\n def base_meth(self, x: int): pass\n"
        "class Child(Base):\n def child_meth(self, y: str): pass\n"
    )

    result = parse_method_signatures(f, "Child")

    assert "child_meth" in result
    assert "base_meth" in result


def test_call_signatures_match_continues_past_unknown_method():
    """
    Given: The call references a method not present in the candidate's signatures.
    When: call_signatures_match is called.
    Then: The unknown method is skipped (returns True — no proven incompatibility).
    """
    calls = {"missing_meth": _CallInfo([None], frozenset())}
    sigs = {"other_meth": _MethodSig([None], 1, 1, frozenset({"x"}), False)}

    assert call_signatures_match(calls, sigs) is True


def test_call_signatures_match_returns_false_for_too_few_positional_args():
    """
    Given: The call passes fewer positional args than the method requires.
    When: call_signatures_match is called.
    Then: Returns False.
    """
    calls = {"meth": _CallInfo([], frozenset())}
    sigs = {"meth": _MethodSig([None, None], 2, 2, frozenset({"a", "b"}), False)}

    assert call_signatures_match(calls, sigs) is False


def test_call_signatures_match_returns_false_for_too_many_positional_args():
    """
    Given: The call passes more positional args than the method accepts.
    When: call_signatures_match is called.
    Then: Returns False.
    """
    calls = {"meth": _CallInfo([None, None], frozenset())}
    sigs = {"meth": _MethodSig([None], 1, 1, frozenset({"a"}), False)}

    assert call_signatures_match(calls, sigs) is False


def test_parse_module_definitions_skips_too_deep_relative_import(tmp_path):
    """
    Given: An __init__.py with a relative import whose level exceeds the module depth.
    When: parse_module_definitions is called.
    Then: The over-deep import is silently skipped.
    """
    init = tmp_path / "__init__.py"
    init.write_text("from .. import foo\n")

    _, reexports = parse_module_definitions(init, "package")

    assert "foo" not in reexports
