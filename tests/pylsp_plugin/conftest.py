from pathlib import Path
from unittest.mock import Mock

import pytest
from pylsp import uris
from pylsp.config.config import Config
from pylsp.workspace import Document, Workspace


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
    return Document(uris.from_fs_path(str(path)), workspace, source=source)
