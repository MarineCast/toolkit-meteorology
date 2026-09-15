"""Installed CLI; acquisition is explicit and product builds are offline."""
from __future__ import annotations

import argparse
import importlib
from importlib.resources import files
import os
from pathlib import Path
import sys

FAMILIES = {
    "spatial-support": "spatial_support",
    "surface-weather": "surface_weather",
    "daylight": "daylight",
    "lunar": "lunar",
}


def initialize_workspace(root: Path) -> list[Path]:
    """Copy packaged defaults without replacing existing user configuration."""
    created = []
    def visit(source, relative: Path):
        for item in sorted(source.iterdir(), key=lambda item: item.name):
            target = root / relative / item.name
            if item.is_dir():
                visit(item, relative / item.name)
            elif not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("xb") as handle:
                    handle.write(item.read_bytes())
                created.append(target)
    visit(files("meteorology").joinpath("resources/config"), Path("config"))
    return created


def _invoke(module: str, arguments: list[str]) -> int:
    previous = sys.argv
    sys.argv = [module, *arguments]
    try:
        return int(importlib.import_module(module).main() or 0)
    finally:
        sys.argv = previous


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, help="Data/config root (or METEOROLOGY_WORKSPACE).")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="Initialize configuration; preserve existing files.")
    commands.add_parser("stages", help="List offline product build order.")
    for action in ("build", "inspect", "download"):
        sub = commands.add_parser(action, help=f"{action.title()} one product; use FAMILY --help for options.")
        sub.add_argument("family", choices=("surface-weather",) if action == "download" else FAMILIES)
        sub.add_argument("arguments", nargs=argparse.REMAINDER)
    for action in ("verify", "catalog", "feature-policy", "benchmark", "migrate-legacy"):
        sub = commands.add_parser(action, add_help=False)
        sub.add_argument("arguments", nargs=argparse.REMAINDER)
    args, extra = parser.parse_known_args(argv)
    if extra:
        # Forward --help and other flags for commands with no family argument.
        if args.command in {"verify", "catalog", "feature-policy", "benchmark", "migrate-legacy"}:
            args.arguments = extra + args.arguments
        else:
            parser.error(f"unrecognized arguments: {' '.join(extra)}")
    old = os.environ.get("METEOROLOGY_WORKSPACE")
    if args.workspace is not None:
        os.environ["METEOROLOGY_WORKSPACE"] = str(args.workspace.expanduser().resolve())
    try:
        from .core.config.paths import project_root
        if args.command == "init":
            created = initialize_workspace(project_root())
            print(f"Initialized {project_root()}: {len(created)} files created")
            return 0
        if args.command == "stages":
            print("spatial-support\nsurface-weather (requires acquired HRRR samples)\ndaylight\nlunar")
            return 0
        if args.command in {"build", "inspect", "download"}:
            return _invoke(f"meteorology.{FAMILIES[args.family]}.{args.command}", args.arguments)
        modules = {
            "verify": "surface_weather.verify",
            "catalog": "maintenance.update_environment_feature_catalog",
            "feature-policy": "modeling.feature_policy",
            "benchmark": "maintenance.benchmark_hrrr_r5_acquisition",
            "migrate-legacy": "migration",
        }
        return _invoke(f"meteorology.{modules[args.command]}", args.arguments)
    finally:
        if old is None:
            os.environ.pop("METEOROLOGY_WORKSPACE", None)
        else:
            os.environ["METEOROLOGY_WORKSPACE"] = old
