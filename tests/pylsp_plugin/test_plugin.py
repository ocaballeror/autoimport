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


def test_missing_import_diagnostic_produces_targeted_quickfix(config, workspace):
    document = make_document(workspace, "a.py", "os.getcwd()\n")
    diagnostic = _undefined_name_diagnostic("os")

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
    assert "`os`" in action["title"]
    assert action["diagnostics"] == [diagnostic]
    assert action["command"]["command"] == plugin.COMMAND_FIX_IMPORTS
    assert action["command"]["arguments"] == [document.uri, "os"]


def test_pyflakes_single_quoted_name_is_extracted(config, workspace):
    document = make_document(workspace, "a.py", "os.getcwd()\n")
    context = {"diagnostics": [{"source": "pyflakes", "message": "undefined name 'os'"}]}

    actions = plugin.pylsp_code_actions(
        config=config,
        workspace=workspace,
        document=document,
        range=SELECTION,
        context=context,
    )

    assert len(actions) == 1
    assert actions[0]["command"]["arguments"] == [document.uri, "os"]


def test_multiple_diagnostics_yield_one_action_per_unique_name(config, workspace):
    document = make_document(workspace, "a.py", "os.getcwd(); sys.exit()\n")
    diagnostics = [
        _undefined_name_diagnostic("os"),
        _undefined_name_diagnostic("sys"),
        _undefined_name_diagnostic("os"),  # duplicate
    ]

    actions = plugin.pylsp_code_actions(
        config=config,
        workspace=workspace,
        document=document,
        range=SELECTION,
        context={"diagnostics": diagnostics},
    )

    names = sorted(action["command"]["arguments"][1] for action in actions)
    assert names == ["os", "sys"]


def test_diagnostic_without_quoted_name_is_skipped(config, workspace):
    document = make_document(workspace, "a.py", "x = 1\n")
    context = {
        "diagnostics": [
            {"source": "pyflakes", "code": "F821", "message": "undefined name (unknown)"}
        ]
    }

    actions = plugin.pylsp_code_actions(
        config=config,
        workspace=workspace,
        document=document,
        range=SELECTION,
        context=context,
    )

    assert actions == []


def test_pylsp_commands_exposes_command(config, workspace):
    assert plugin.COMMAND_FIX_IMPORTS in plugin.pylsp_commands(config, workspace)


def test_execute_command_passes_only_names(config, workspace):
    source = "os.getcwd()\n"
    document = make_document(workspace, "a.py", source)
    fixed = "import os\n\nos.getcwd()\n"

    captured = {}

    def fake_fix_files(paths, _config, only_names=None):
        captured["only_names"] = only_names
        for p in paths:
            p.write_text(fixed, encoding="utf-8")

    with patch.object(plugin, "fix_files", side_effect=fake_fix_files):
        plugin.pylsp_execute_command(
            config=config,
            workspace=workspace,
            command=plugin.COMMAND_FIX_IMPORTS,
            arguments=[document.uri, "os"],
        )

    assert captured["only_names"] == {"os"}
    workspace._endpoint.request.assert_called_once()
    method, payload = workspace._endpoint.request.call_args[0]
    assert method == "workspace/applyEdit"
    edits = payload["edit"]["changes"][document.uri]
    assert edits[0]["newText"] == fixed
    assert edits[0]["range"]["start"] == {"line": 0, "character": 0}
    assert edits[0]["range"]["end"] == {"line": 1, "character": 0}


def test_execute_command_without_name_runs_full_fix(config, workspace):
    document = make_document(workspace, "a.py", "os.getcwd()\n")

    captured = {}

    def fake_fix_files(paths, _config, only_names=None):
        captured["only_names"] = only_names

    with patch.object(plugin, "fix_files", side_effect=fake_fix_files):
        plugin.pylsp_execute_command(
            config=config,
            workspace=workspace,
            command=plugin.COMMAND_FIX_IMPORTS,
            arguments=[document.uri],
        )

    assert captured["only_names"] is None


def test_execute_command_no_change_skips_edit(config, workspace):
    source = "import os\n\nos.getcwd()\n"
    document = make_document(workspace, "a.py", source)

    def noop_fix(paths, _config, only_names=None):
        return None

    with patch.object(plugin, "fix_files", side_effect=noop_fix):
        plugin.pylsp_execute_command(
            config=config,
            workspace=workspace,
            command=plugin.COMMAND_FIX_IMPORTS,
            arguments=[document.uri, "os"],
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
