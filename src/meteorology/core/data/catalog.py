from __future__ import annotations

import pyarrow as pa

from .contracts import DatasetFormat, DatasetId, DatasetLayer, DatasetSpec, ProcessingMode
from .meteorological_schemas import (
    DAYLIGHT_DOY_SCHEMA,
    DAYLIGHT_SCHEMA,
    HRRR_CROSSWALK_SCHEMA,
    HRRR_INVENTORY_SCHEMA,
    HRRR_SAMPLE_SCHEMA,
    LUNAR_SCHEMA,
    SUPPORT_SCHEMA,
    SURFACE_WEATHER_DAILY_SCHEMA,
)
from .registry import DATASETS

def _register(
    dataset_id: str,
    layer: DatasetLayer,
    format: DatasetFormat,
    path: str,
    producer: str,
    *,
    dependencies: tuple[str, ...] = (),
    schema: pa.Schema | None = None,
    primary_key: tuple[str, ...] = (),
    partition_keys: tuple[str, ...] = (),
    modes: tuple[ProcessingMode, ...] | None = None,
    schema_version: str = "1",
) -> None:
    DATASETS.register(
        DatasetSpec(
            dataset_id=DatasetId(dataset_id),
            layer=layer,
            format=format,
            path_template=path,
            producer=producer,
            dependencies=tuple(DatasetId(item) for item in dependencies),
            schema=schema,
            primary_key=primary_key,
            partition_keys=partition_keys,
            schema_version=schema_version,
            allowed_modes=modes or (ProcessingMode.RETROSPECTIVE, ProcessingMode.AS_OF),
        )
    )


def register_builtin_datasets() -> None:
    if tuple(DATASETS):
        return
    for resolution in (4, 5, 6):
        _register(
            f"environment.meteorological.spatial_support_r{resolution}",
            DatasetLayer.DOMAIN,
            DatasetFormat.PARQUET,
            "{data_root}/processed/domain/environmental_layer/meteorological/spatial_support/"
            f"H3_MODEL_AREA_SUPPORT_RES_{resolution}.parquet",
            "environment.meteorological.spatial_support.build",
            schema=SUPPORT_SCHEMA,
            primary_key=("H3_INDEX",),
            schema_version="2",
        )
    _register(
        "environment.meteorological.surface_weather.h3_samples_r5",
        DatasetLayer.SOURCE,
        DatasetFormat.DIRECTORY,
        "{data_root}/raw/environment/meteorological/surface_weather/hrrr/samples",
        "environment.meteorological.surface_weather.download",
        dependencies=("environment.meteorological.spatial_support_r5",),
        schema=HRRR_SAMPLE_SCHEMA,
        primary_key=("H3_INDEX", "VALID_TIME_UTC"),
        partition_keys=("YEAR", "DATE"),
        schema_version="3",
    )
    _register(
        "environment.meteorological.surface_weather.source_inventory",
        DatasetLayer.SOURCE,
        DatasetFormat.PARQUET,
        "{data_root}/raw/environment/meteorological/surface_weather/hrrr/"
        "HRRR_R5_SOURCE_INVENTORY.parquet",
        "environment.meteorological.surface_weather.download",
        dependencies=("environment.meteorological.surface_weather.h3_samples_r5",),
        schema=HRRR_INVENTORY_SCHEMA,
        primary_key=("VALID_TIME_UTC",),
        schema_version="3",
    )
    _register(
        "environment.meteorological.surface_weather.h3_daily_r5",
        DatasetLayer.DOMAIN,
        DatasetFormat.DIRECTORY,
        "{data_root}/processed/domain/environmental_layer/meteorological/surface_weather/"
        "H3_SURFACE_WEATHER_DAILY_RES_5",
        "environment.meteorological.surface_weather.build",
        dependencies=(
            "environment.meteorological.surface_weather.source_inventory",
            "environment.meteorological.spatial_support_r5",
        ),
        schema=SURFACE_WEATHER_DAILY_SCHEMA,
        primary_key=("H3_INDEX", "DATE"),
        partition_keys=("YEAR", "DATE"),
        schema_version="3",
    )
    _register(
        "environment.meteorological.daylight.h3_daily_r4",
        DatasetLayer.DOMAIN,
        DatasetFormat.DIRECTORY,
        "{data_root}/processed/domain/environmental_layer/meteorological/daylight/"
        "H3_DAYLIGHT_DAILY_RES_4",
        "environment.meteorological.daylight.build",
        dependencies=("environment.meteorological.spatial_support_r4",),
        schema=DAYLIGHT_SCHEMA,
        primary_key=("H3_INDEX", "DATE"),
        partition_keys=("YEAR",),
        schema_version="2",
    )
    _register(
        "environment.meteorological.surface_weather.h3_crosswalk_r5",
        DatasetLayer.DOMAIN,
        DatasetFormat.DIRECTORY,
        "{data_root}/processed/domain/environmental_layer/meteorological/surface_weather/"
        "H3_HRRR_NEAREST_GRID_RES_5",
        "environment.meteorological.surface_weather.build",
        dependencies=("environment.meteorological.spatial_support_r5",),
        schema=HRRR_CROSSWALK_SCHEMA,
        primary_key=("H3_INDEX", "SOURCE_GRID_HASH"),
        partition_keys=("SOURCE_GRID_HASH",),
        schema_version="3",
    )
    _register(
        "environment.meteorological.daylight.h3_doy_r4",
        DatasetLayer.DOMAIN,
        DatasetFormat.PARQUET,
        "{data_root}/processed/domain/environmental_layer/meteorological/daylight/"
        "H3_DAYLIGHT_DOY_RES_4.parquet",
        "environment.meteorological.daylight.build",
        dependencies=("environment.meteorological.spatial_support_r4",),
        schema=DAYLIGHT_DOY_SCHEMA,
        primary_key=("H3_INDEX", "DAY_OF_YEAR"),
        schema_version="2",
    )
    _register(
        "environment.meteorological.lunar.h3_daily_r5",
        DatasetLayer.DOMAIN,
        DatasetFormat.DIRECTORY,
        "{data_root}/processed/domain/environmental_layer/meteorological/lunar/"
        "H3_LUNAR_DAILY_RES_5",
        "environment.meteorological.lunar.build",
        dependencies=("environment.meteorological.spatial_support_r5",),
        schema=LUNAR_SCHEMA,
        primary_key=("H3_INDEX", "DATE"),
        partition_keys=("YEAR",),
        schema_version="2",
    )

