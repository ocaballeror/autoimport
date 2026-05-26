from unittest.mock import patch

from autoimport.pylsp_plugin import plugin
from tests.pylsp_plugin.conftest import make_document

SELECTION = {
    "start": {"line": 0, "character": 0},
    "end": {"line": 0, "character": 1},
}


def _undefined_name_diagnostic(name: str = "os") -> dict:
    return {
        "source": "pyflakes",
        "code": "F821",
        "message": f"undefined name `{name}`",
        "range": SELECTION,
    }


def _patch_candidates(mapping):
    """Stub PackageFinder so code_actions doesn't touch the real filesystem."""

    def side_effect(name, _file):
        return mapping.get(name, [])

    return patch.object(
        plugin.PackageFinder, "find_candidates", autospec=False, side_effect=side_effect
    )


def _patch_index():
    return patch.object(plugin.PackageFinder, "index_packages", autospec=False)


def _quickfix_actions(actions: list[dict]) -> list[dict]:
    return [a for a in actions if a.get("kind") == "quickfix"]


def _fix_all_action(actions: list[dict]) -> dict | None:
    for action in actions:
        if action["command"]["command"] == plugin.COMMAND_FIX_ALL_IMPORTS:
            return action
    return None


def test_no_diagnostics_yields_only_fix_all(config, workspace):
    document = make_document(workspace, "a.py", "x = 1\n")

    actions = plugin.pylsp_code_actions(
        config=config,
        workspace=workspace,
        document=document,
        range=SELECTION,
        context={"diagnostics": []},
    )

    assert _quickfix_actions(actions) == []
    fix_all = _fix_all_action(actions)
    assert fix_all is not None
    assert fix_all["command"]["arguments"] == [document.uri]


def test_unrelated_diagnostic_yields_only_fix_all(config, workspace):
    document = make_document(workspace, "a.py", "x = 1\n")
    context = {
        "diagnostics": [{"source": "pycodestyle", "code": "E501", "message": "line too long"}]
    }

    actions = plugin.pylsp_code_actions(
        config=config,
        workspace=workspace,
        document=document,
        range=SELECTION,
        context=context,
    )

    assert _quickfix_actions(actions) == []
    assert _fix_all_action(actions) is not None


def test_single_candidate_produces_one_action(config, workspace):
    document = make_document(workspace, "a.py", "os.getcwd()\n")
    diagnostic = _undefined_name_diagnostic("os")

    with _patch_index(), _patch_candidates({"os": ["import os"]}):
        actions = plugin.pylsp_code_actions(
            config=config,
            workspace=workspace,
            document=document,
            range=SELECTION,
            context={"diagnostics": [diagnostic]},
        )

    quickfixes = _quickfix_actions(actions)
    assert len(quickfixes) == 1
    action = quickfixes[0]
    assert action["kind"] == "quickfix"
    assert "import os" in action["title"]
    assert action["diagnostics"] == [diagnostic]
    assert action["command"]["command"] == plugin.COMMAND_FIX_IMPORTS
    assert action["command"]["arguments"] == [document.uri, "import os"]


def test_multiple_candidates_produce_one_action_per_candidate(config, workspace):
    document = make_document(workspace, "a.py", "Book()\n")
    diagnostic = _undefined_name_diagnostic("Book")

    candidates = ["from my_app.models import Book", "from my_app.legacy.models import Book"]

    with _patch_index(), _patch_candidates({"Book": candidates}):
        actions = plugin.pylsp_code_actions(
            config=config,
            workspace=workspace,
            document=document,
            range=SELECTION,
            context={"diagnostics": [diagnostic]},
        )

    quickfixes = _quickfix_actions(actions)
    assert len(quickfixes) == 2
    titles = [action["title"] for action in quickfixes]
    assert all(any(c in t for c in candidates) for t in titles)

    inserts = [action["command"]["arguments"][1] for action in quickfixes]
    assert sorted(inserts) == sorted(candidates)

    # All actions for the same diagnostic should attach it for editor grouping.
    for action in quickfixes:
        assert action["diagnostics"] == [diagnostic]


def test_no_candidates_means_no_action(config, workspace):
    document = make_document(workspace, "a.py", "x = unknown_thing\n")
    diagnostic = _undefined_name_diagnostic("unknown_thing")

    with _patch_index(), _patch_candidates({}):
        actions = plugin.pylsp_code_actions(
            config=config,
            workspace=workspace,
            document=document,
            range=SELECTION,
            context={"diagnostics": [diagnostic]},
        )

    assert _quickfix_actions(actions) == []
    assert _fix_all_action(actions) is not None


def test_per_name_grouping_with_mixed_candidate_counts(config, workspace):
    """Two missing names, one with a single candidate and one with two."""
    document = make_document(workspace, "a.py", "os.getcwd(); Book()\n")
    diagnostics = [
        _undefined_name_diagnostic("os"),
        _undefined_name_diagnostic("Book"),
    ]

    mapping = {
        "os": ["import os"],
        "Book": ["from a.b import Book", "from c.d import Book"],
    }

    with _patch_index(), _patch_candidates(mapping):
        actions = plugin.pylsp_code_actions(
            config=config,
            workspace=workspace,
            document=document,
            range=SELECTION,
            context={"diagnostics": diagnostics},
        )

    inserts = sorted(action["command"]["arguments"][1] for action in _quickfix_actions(actions))
    assert inserts == sorted(["import os", "from a.b import Book", "from c.d import Book"])


def test_pylsp_commands_exposes_command(config, workspace):
    commands = plugin.pylsp_commands(config, workspace)
    assert plugin.COMMAND_FIX_IMPORTS in commands
    assert plugin.COMMAND_FIX_ALL_IMPORTS in commands


def test_fix_all_command_applies_edit(config, workspace):
    source = "os.getcwd()\n"
    new_source = "import os\n\nos.getcwd()\n"
    document = make_document(workspace, "a.py", source)
    document.version = 3

    applied: list[dict] = []
    workspace.apply_edit = applied.append

    with patch.object(plugin, "_fix_all_in_text", return_value=new_source) as fix_all:
        plugin.pylsp_execute_command(
            config=config,
            workspace=workspace,
            command=plugin.COMMAND_FIX_ALL_IMPORTS,
            arguments=[document.uri],
        )

    fix_all.assert_called_once_with(source, workspace.root_path)
    assert len(applied) == 1
    change = applied[0]["documentChanges"][0]
    assert change["textDocument"] == {"uri": document.uri, "version": 3}
    assert _apply_edit(source, change["edits"][0]) == new_source


def test_fix_all_command_skips_apply_when_no_change(config, workspace):
    source = "x = 1\n"
    document = make_document(workspace, "a.py", source)

    applied: list[dict] = []
    workspace.apply_edit = applied.append

    with patch.object(plugin, "_fix_all_in_text", return_value=source):
        plugin.pylsp_execute_command(
            config=config,
            workspace=workspace,
            command=plugin.COMMAND_FIX_ALL_IMPORTS,
            arguments=[document.uri],
        )

    assert applied == []


def _apply_edit(source: str, edit: dict) -> str:
    """Replay an LSP TextEdit against ``source`` so tests can assert on the result."""
    lines = source.splitlines(keepends=True)
    start = edit["range"]["start"]["line"]
    end = edit["range"]["end"]["line"]
    return "".join(lines[:start]) + edit["newText"] + "".join(lines[end:])


def test_execute_command_applies_chosen_import(config, workspace):
    source = "os.getcwd()\n"
    document = make_document(workspace, "a.py", source)
    document.version = 7
    fixed = "import os\n\nos.getcwd()\n"

    captured = {}

    def fake_insert(src, import_statement):
        captured["statement"] = import_statement
        captured["source"] = src
        return fixed

    with patch.object(plugin, "insert_chosen_import_text", side_effect=fake_insert):
        plugin.pylsp_execute_command(
            config=config,
            workspace=workspace,
            command=plugin.COMMAND_FIX_IMPORTS,
            arguments=[document.uri, "import os"],
        )

    assert captured["statement"] == "import os"
    assert captured["source"] == source
    workspace._endpoint.request.assert_called_once()
    method, payload = workspace._endpoint.request.call_args[0]
    assert method == "workspace/applyEdit"

    doc_changes = payload["edit"]["documentChanges"]
    assert len(doc_changes) == 1
    change = doc_changes[0]
    assert change["textDocument"]["uri"] == document.uri
    assert change["textDocument"]["version"] == 7

    edits = change["edits"]
    # The edit must be minimal (no whole-document replace) but produce the fixed source.
    assert _apply_edit(source, edits[0]) == fixed
    assert "os.getcwd()" not in edits[0]["newText"]


def test_execute_command_sends_null_version_when_document_has_none(config, workspace):
    """LSP allows `version: null` for unversioned buffers; we must not break."""
    source = "os.getcwd()\n"
    document = make_document(workspace, "a.py", source)
    document.version = None
    fixed = "import os\n\nos.getcwd()\n"

    with patch.object(plugin, "insert_chosen_import_text", return_value=fixed):
        plugin.pylsp_execute_command(
            config=config,
            workspace=workspace,
            command=plugin.COMMAND_FIX_IMPORTS,
            arguments=[document.uri, "import os"],
        )

    _, payload = workspace._endpoint.request.call_args[0]
    assert payload["edit"]["documentChanges"][0]["textDocument"]["version"] is None


def test_minimal_edit_only_touches_changed_lines():
    """A pure insertion produces a zero-length range edit at the insertion point."""
    old = "def foo():\n    pass\n"
    new = "import os\n\ndef foo():\n    pass\n"

    edit = plugin._minimal_text_edit(old, new)

    assert edit["range"]["start"] == {"line": 0, "character": 0}
    assert edit["range"]["end"] == {"line": 0, "character": 0}
    assert edit["newText"] == "import os\n\n"


def test_minimal_edit_handles_no_change():
    """Identical strings produce an empty edit at (0, 0)."""
    edit = plugin._minimal_text_edit("x = 1\n", "x = 1\n")

    assert edit["newText"] == ""


def test_execute_command_without_import_statement_is_noop(config, workspace):
    document = make_document(workspace, "a.py", "os.getcwd()\n")

    with patch.object(plugin, "insert_chosen_import_text") as mock_insert:
        plugin.pylsp_execute_command(
            config=config,
            workspace=workspace,
            command=plugin.COMMAND_FIX_IMPORTS,
            arguments=[document.uri],
        )

    mock_insert.assert_not_called()
    workspace._endpoint.request.assert_not_called()


def test_execute_command_no_change_skips_edit(config, workspace):
    source = "import os\n\nos.getcwd()\n"
    document = make_document(workspace, "a.py", source)

    with patch.object(plugin, "insert_chosen_import_text", return_value=source):
        plugin.pylsp_execute_command(
            config=config,
            workspace=workspace,
            command=plugin.COMMAND_FIX_IMPORTS,
            arguments=[document.uri, "import os"],
        )

    workspace._endpoint.request.assert_not_called()


def test_execute_command_ignores_unknown_command(config, workspace):
    result = plugin.pylsp_execute_command(
        config=config,
        workspace=workspace,
        command="some.other.command",
        arguments=[],
    )

    assert result is None
    workspace._endpoint.request.assert_not_called()
