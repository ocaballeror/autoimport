"""Command line interface definition."""

import logging
from pathlib import Path
import click
import xdg_base_dirs
from maison import UserConfig

from autoimport.fix import fix_files

log = logging.getLogger(__name__)


@click.command()
@click.version_option()
@click.option("--config-file", default=None)
@click.argument("files", type=Path, nargs=-1)
def cli(
    files: tuple[Path, ...],
    config_file: str | None = None,
) -> None:
    """Corrects the source code of the specified files."""
    config_files: list[str] = []

    global_config_path = xdg_base_dirs.xdg_config_home() / "autoimport" / "config.toml"
    if global_config_path.is_file():
        config_files.append(str(global_config_path))

    config_files.append("pyproject.toml")

    if config_file is not None:
        config_files.append(config_file)

    config = UserConfig(
        package_name="autoimport", source_files=config_files, merge_configs=True
    ).values

    fix_files(list(files), config)


if __name__ == "__main__":  # pragma: no cover
    cli()
