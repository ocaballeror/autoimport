from pathlib import Path
from unittest.mock import Mock

import pytest
from pylsp import uris
from pylsp.config.config import Config
from pylsp.workspace import Document, Workspace

from autoimport.pylsp_plugin import plugin


@pytest.fixture(autouse=True)
def _reset_plugin_caches():
    """Module-level caches must not leak between tests."""
    plugin._finder_cache.clear()
    plugin._config_cache.clear()
    yield
    plugin._finder_cache.clear()
    plugin._config_cache.clear()


@pytest.fixture
def workspace(tmp_path):
    ws = Workspace(uris.from_fs_path(str(tmp_path)), Mock())
    ws._config = Config(ws.root_uri, {}, 0, {})
    return ws


@pytest.fixture
def config(workspace):
    return Config(workspace.root_uri, {}, 0, {})


def make_document(workspace: Workspace, name: str, source: str) -> Document:
    path = Path(workspace.root_path) / name
    path.write_text(source, encoding="utf-8")
    document = Document(uris.from_fs_path(str(path)), workspace, source=source)
    # Register with the workspace so workspace.get_document(uri) returns
    # *this* instance (with whatever version/source the test set).
    workspace._docs[document.uri] = document
    return document
