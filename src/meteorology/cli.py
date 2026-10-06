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
    "hourly-weather": "hourly_weather",
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
    parser.add_argument("--study-config", type=Path, help="Explicit shared study JSON (or MARINECAST_STUDY_CONFIG); no implicit lookup.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="Initialize configuration; preserve existing files.")
    commands.add_parser("study-preflight", help="Validate the selected study for planning; no files or provider requests.")
    commands.add_parser("stages", help="List offline product build order.")
    for action in ("build", "inspect", "download"):
        sub = commands.add_parser(action, help=f"{action.title()} one product; use FAMILY --help for options.")
        sub.add_argument("family", choices=("surface-weather", "hourly-weather") if action == "download" else FAMILIES)
        sub.add_argument("arguments", nargs=argparse.REMAINDER)
    for action in ("verify", "catalog", "feature-policy", "benchmark", "migrate-legacy", "validate", "variables", "freeze-release", "example-offline", "export-weather-summary", "demo-hourly-week"):
        sub = commands.add_parser(action, add_help=False)
        sub.add_argument("arguments", nargs=argparse.REMAINDER)
    matrix = commands.add_parser("export-daily-matrix", help="Combine native daily weather and astronomy.")
    matrix.add_argument("--manifest", type=Path, action="append", required=True)
    matrix.add_argument("--output", type=Path, required=True)
    args, extra = parser.parse_known_args(argv)
    if extra:
        # Forward --help and other flags for commands with no family argument.
        if args.command in {"verify", "catalog", "feature-policy", "benchmark", "migrate-legacy", "validate", "variables", "freeze-release", "example-offline", "export-weather-summary", "demo-hourly-week"}:
            args.arguments = extra + args.arguments
        else:
            parser.error(f"unrecognized arguments: {' '.join(extra)}")
    old_study = os.environ.get("MARINECAST_STUDY_CONFIG")
    if args.study_config is not None:
        os.environ["MARINECAST_STUDY_CONFIG"] = str(args.study_config.expanduser().resolve())
    old = os.environ.get("METEOROLOGY_WORKSPACE")
    if args.workspace is not None:
        os.environ["METEOROLOGY_WORKSPACE"] = str(args.workspace.expanduser().resolve())
    try:
        if args.command == "study-preflight":
            import json
            from .study import planning_report
            print(json.dumps(planning_report(args.study_config), indent=2))
            return 0
        from .core.config.paths import project_root
        if args.command == "export-daily-matrix":
            from .daily_matrix import export
            print(export(args.manifest, args.output))
            return 0
        if args.command == "init":
            created = initialize_workspace(project_root())
            print(f"Initialized {project_root()}: {len(created)} files created")
            return 0
        if args.command == "stages":
            print("spatial-support\nsurface-weather (requires acquired HRRR samples)\nhourly-weather (requires retained decoded f00 grids)\ndaylight\nlunar")
            return 0
        if args.command in {"build", "inspect", "download"}:
            return _invoke(f"meteorology.{FAMILIES[args.family]}.{args.command}", args.arguments)
        modules = {
            "verify": "surface_weather.verify",
            "catalog": "maintenance.update_environment_feature_catalog",
            "feature-policy": "modeling.feature_policy",
            "benchmark": "maintenance.benchmark_hrrr_r5_acquisition",
            "migrate-legacy": "migration",
            "validate": "validation",
            "variables": "variables",
            "freeze-release": "releases",
            "example-offline": "offline_example",
            "export-weather-summary": "weather_summary",
            "demo-hourly-week": "week_demo",
        }
        return _invoke(f"meteorology.{modules[args.command]}", args.arguments)
    except (ValueError, FileNotFoundError, FileExistsError, RuntimeError, ImportError) as exc:
        parser.exit(2, f"meteorology: {exc}\n")
    finally:
        if old_study is None:
            os.environ.pop("MARINECAST_STUDY_CONFIG", None)
        else:
            os.environ["MARINECAST_STUDY_CONFIG"] = old_study
        if old is None:
            os.environ.pop("METEOROLOGY_WORKSPACE", None)
        else:
            os.environ["METEOROLOGY_WORKSPACE"] = old
