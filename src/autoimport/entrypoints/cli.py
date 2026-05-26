"""Command line interface definition."""

import sys
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

import click

from autoimport.config import load_config
from autoimport.fix import fix_files


def _run_on_stdin(config: dict[str, Any]) -> None:
    source = sys.stdin.read()
    # The temp file is created in cwd so pyprojroot picks up the active project
    # rather than the system temp directory.
    tmp = NamedTemporaryFile("w", suffix=".py", dir=".", delete=False, encoding="utf-8")
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
    config = load_config(extra_config_file=config_file)

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
