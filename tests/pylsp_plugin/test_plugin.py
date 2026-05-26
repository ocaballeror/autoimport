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


def test_no_diagnostics_means_no_action(config, workspace):
    document = make_document(workspace, "a.py", "x = 1\n")

    actions = plugin.pylsp_code_actions(
        config=config,
        workspace=workspace,
        document=document,
        range=SELECTION,
        context={"diagnostics": []},
    )

    assert actions == []


def test_unrelated_diagnostic_means_no_action(config, workspace):
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

    assert actions == []


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

    assert len(actions) == 1
    action = actions[0]
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

    assert len(actions) == 2
    titles = [action["title"] for action in actions]
    assert all(any(c in t for c in candidates) for t in titles)

    inserts = [action["command"]["arguments"][1] for action in actions]
    assert sorted(inserts) == sorted(candidates)

    # All actions for the same diagnostic should attach it for editor grouping.
    for action in actions:
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

    assert actions == []


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

    inserts = sorted(action["command"]["arguments"][1] for action in actions)
    assert inserts == sorted(
        ["import os", "from a.b import Book", "from c.d import Book"]
    )


def test_pylsp_commands_exposes_command(config, workspace):
    assert plugin.COMMAND_FIX_IMPORTS in plugin.pylsp_commands(config, workspace)


def test_execute_command_applies_chosen_import(config, workspace):
    source = "os.getcwd()\n"
    document = make_document(workspace, "a.py", source)
    fixed = "import os\n\nos.getcwd()\n"

    captured = {}

    def fake_insert(path, import_statement):
        captured["statement"] = import_statement
        path.write_text(fixed, encoding="utf-8")

    with patch.object(plugin, "insert_chosen_import", side_effect=fake_insert):
        plugin.pylsp_execute_command(
            config=config,
            workspace=workspace,
            command=plugin.COMMAND_FIX_IMPORTS,
            arguments=[document.uri, "import os"],
        )

    assert captured["statement"] == "import os"
    workspace._endpoint.request.assert_called_once()
    method, payload = workspace._endpoint.request.call_args[0]
    assert method == "workspace/applyEdit"
    edits = payload["edit"]["changes"][document.uri]
    assert edits[0]["newText"] == fixed


def test_execute_command_without_import_statement_is_noop(config, workspace):
    document = make_document(workspace, "a.py", "os.getcwd()\n")

    with patch.object(plugin, "insert_chosen_import") as mock_insert:
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

    def noop_insert(path, _import_statement):
        return None

    with patch.object(plugin, "insert_chosen_import", side_effect=noop_insert):
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
