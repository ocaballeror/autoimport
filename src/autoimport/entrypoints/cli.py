"""Command line interface definition."""

import sys
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

import click
import xdg_base_dirs
from maison import UserConfig

from autoimport.fix import fix_files


def _load_config(config_file: str | None) -> dict[str, Any]:
    config_files: list[str] = []

    global_config_path = xdg_base_dirs.xdg_config_home() / "autoimport" / "config.toml"
    if global_config_path.is_file():
        config_files.append(str(global_config_path))

    config_files.append("pyproject.toml")

    if config_file is not None:
        config_files.append(config_file)

    return UserConfig(
        package_name="autoimport", source_files=config_files, merge_configs=True
    ).values


def _run_on_stdin(config: dict[str, Any]) -> None:
    source = sys.stdin.read()
    # The temp file is created in cwd so pyprojroot picks up the active project
    # rather than the system temp directory.
    tmp = NamedTemporaryFile(
        "w", suffix=".py", dir=".", delete=False, encoding="utf-8"
    )
    try:
        tmp.write(source)
        tmp.close()
        tmp_path = Path(tmp.name)
        fix_files([tmp_path], config)
        sys.stdout.write(tmp_path.read_text(encoding="utf-8"))
    finally:
        Path(tmp.name).unlink(missing_ok=True)


@click.command()
@click.version_option()
@click.option("--config-file", default=None)
@click.argument("files", nargs=-1)
def cli(
    files: tuple[str, ...],
    config_file: str | None = None,
) -> None:
    """Corrects the source code of the specified files.

    Pass ``-`` as the only file argument to read from stdin and write the
    corrected source to stdout.
    """
    config = _load_config(config_file)

    if files == ("-",):
        _run_on_stdin(config)
        return

    paths: list[Path] = []
    for arg in files:
        if arg == "-":
            raise click.BadParameter(
                "'-' (stdin) cannot be mixed with other paths", param_hint="FILES"
            )
        path = Path(arg)
        if not path.exists():
            raise click.BadParameter(f"path does not exist: {arg}", param_hint="FILES")
        paths.append(path)

    fix_files(paths, config)


if __name__ == "__main__":  # pragma: no cover
    cli()
