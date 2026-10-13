"""Public entry points for reproducible meteorological and astronomical products.

The functions import their implementation lazily so ``import meteorology`` stays
light and does not load geospatial or acquisition libraries before use.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Sequence

from ._version import __version__

if TYPE_CHECKING:
    from .config import MeteorologicalConfig

DEFAULT_CONFIG = Path("config/data/environment_meteorological.yaml")


def load_config(path: str | Path = DEFAULT_CONFIG) -> MeteorologicalConfig:
    """Validate and load an initialized workspace configuration."""

    from .config import load_meteorological_config

    return load_meteorological_config(path)


def build_spatial_support(config_path: str | Path = DEFAULT_CONFIG, *, run_id: str | None = None) -> tuple[Path, ...]:
    """Build configured WGS84-bounding-box H3 centroid support."""

    from .spatial_support.build import build_meteorological_spatial_support

    return build_meteorological_spatial_support(config_path, run_id=run_id)


def download_surface_weather(config_path: str | Path = DEFAULT_CONFIG, *, start_date: str | None = None,
                             end_date: str | None = None, workers: int = 4,
                             dry_run: bool = False, allow_large_download: bool = False) -> dict[str, object]:
    """Acquire a bounded HRRR range, or preview it without network access."""

    from .surface_weather.download import download_surface_weather as implementation

    return implementation(config_path, start_date=start_date, end_date=end_date,
                          max_workers=workers, dry_run=dry_run,
                          allow_large_download=allow_large_download)


def build_surface_weather(config_path: str | Path = DEFAULT_CONFIG, *, start_date: str | None = None,
                          end_date: str | None = None, run_id: str | None = None) -> tuple[Path, ...]:
    """Build strict retrospective daily weather from a complete local acquisition."""

    from .surface_weather.build import build_surface_weather as implementation

    return implementation(config_path, start_date=start_date, end_date=end_date, run_id=run_id)


def acquire_hourly_weather(config_path: str | Path = DEFAULT_CONFIG, *, local_date: str,
                           decoded_dir: str | Path, dry_run: bool = False) -> dict:
    """Ingest a complete local day of retained decoded f00 grids without network access."""

    from .hourly_weather import acquire_hourly_weather as implementation

    return implementation(config_path, local_date=local_date, decoded_dir=decoded_dir,
                          dry_run=dry_run)


def build_hourly_weather(config_path: str | Path = DEFAULT_CONFIG, *,
                         acquisition_manifest: str | Path | None = None) -> Path:
    """Publish a separate hourly H3 atmosphere family from its pinned acquisition."""

    from .hourly_weather import build_hourly_weather as implementation

    return implementation(config_path, acquisition_manifest=acquisition_manifest)


def build_daylight(config_path: str | Path = DEFAULT_CONFIG, *, start_date: str | None = None,
                   end_date: str | None = None, run_id: str | None = None) -> tuple[Path, ...]:
    """Build deterministic daily solar context at the configured H3 resolution."""

    from .daylight.build import build_daylight as implementation

    return implementation(config_path, start_date=start_date, end_date=end_date, run_id=run_id)


def build_lunar(config_path: str | Path = DEFAULT_CONFIG, *, start_date: str | None = None,
                end_date: str | None = None, run_id: str | None = None) -> tuple[Path, ...]:
    """Build deterministic approximate daily lunar context."""

    from .lunar.build import build_lunar as implementation

    return implementation(config_path, start_date=start_date, end_date=end_date, run_id=run_id)


def export_daily_matrix(manifest_paths: Sequence[str | Path], output: str | Path) -> Path:
    """Combine validated daily families at their native H3 resolutions."""

    from .daily_matrix import export

    return export([Path(path) for path in manifest_paths], Path(output))


def export_weather_summary(manifest_paths: Sequence[str | Path], output_dir: str | Path, *,
                           spatial_scope: str = "both", as_of_utc: str | None = None) -> Path:
    """Export compact daily/weekly native-H3 and regional retrospective context."""
    from .weather_summary import export_weather_summary as implementation

    return implementation(list(manifest_paths), output_dir,
                          spatial_scope=spatial_scope, as_of_utc=as_of_utc)


def validate_product(manifest_path: str | Path) -> dict:
    """Validate a family manifest, checksums, schemas, keys and values."""

    from .validation import validate_product as implementation

    return implementation(manifest_path)


def freeze_release(manifest_path: str | Path, output_root: str | Path) -> Path:
    """Copy a validated family and its declared inputs to a new release directory."""

    from .releases import freeze_release as implementation

    return implementation(manifest_path, output_root)


__all__ = [
    "__version__", "load_config", "build_spatial_support", "download_surface_weather",
    "build_surface_weather", "build_daylight", "build_lunar", "export_daily_matrix",
    "acquire_hourly_weather", "build_hourly_weather", "validate_product", "freeze_release",
    "export_weather_summary",
]
