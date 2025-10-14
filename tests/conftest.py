"""Store the classes and fixtures used throughout the tests."""

import pathlib
import shutil
from pathlib import Path

import pytest


@pytest.fixture()
def test_dir(tmp_path: Path) -> pathlib.Path:
    """Creates test directory and files and returns root test file directory."""
    file_contents = "os.getcwd()"

    test_dirs = tmp_path / "test_files"
    subdir = test_dirs / "subdir"

    subdir.mkdir(parents=True)

    file1 = test_dirs / "test_file1.py"
    with file1.open("w", encoding="UTF8") as file_descriptor:
        file_descriptor.write(file_contents)

    file2 = subdir / "test_file2.py"
    with file2.open("w", encoding="UTF8") as file_descriptor:
        file_descriptor.write(file_contents)

    return test_dirs


@pytest.fixture(autouse=True)
def clear_pytest_cache():
    yield  # let the test run
    shutil.rmtree(".autoimport_cache", ignore_errors=True)
