"""Regenerate the unified, artifact-verified environment feature catalog."""

from __future__ import annotations

import argparse
import runpy
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import yaml

from meteorology.core.config.paths import project_root

DEFAULT_OUTPUT_PATH = Path("config/feature_catalog.yaml")
KEY_COLUMNS = {"H3_INDEX", "DATE"}


@dataclass(frozen=True)
class MeteorologicalProduct:
    common_name: str
    subfamily: str
    paths: dict[int, str]
    producer: str
    grain: str
    partition_keys: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    index_columns: tuple[str, ...] = ("H3_INDEX", "DATE")


METEOROLOGICAL_PRODUCTS = {
    "meteorological_spatial_support": MeteorologicalProduct(
        "Atmospheric model-area H3 support",
        "spatial_support",
        {
            resolution: "data/processed/domain/environmental_layer/meteorological/"
            f"spatial_support/H3_MODEL_AREA_SUPPORT_RES_{resolution}.parquet"
            for resolution in (4, 5, 6)
        },
        "meteorology.spatial_support.build",
        "one row per full-bbox atmospheric-support H3 cell",
        index_columns=("H3_INDEX",),
    ),
    "surface_weather_daily": MeteorologicalProduct(
        "Daily HRRR surface weather",
        "surface_weather",
        {
            5: "data/processed/domain/environmental_layer/meteorological/surface_weather/"
            "H3_SURFACE_WEATHER_DAILY_RES_5"
        },
        "meteorology.surface_weather.build",
        "one row per H3 cell and local date",
        ("YEAR", "DATE"),
        ("hrrr_source_inventory", "meteorological_spatial_support"),
    ),
    "daylight_daily": MeteorologicalProduct(
        "Daily daylight and solar profile",
        "daylight",
        {
            4: "data/processed/domain/environmental_layer/meteorological/daylight/"
            "H3_DAYLIGHT_DAILY_RES_4"
        },
        "meteorology.daylight.build",
        "one row per H3 cell and local date",
        ("YEAR",),
        ("meteorological_spatial_support",),
    ),
    "daylight_day_of_year": MeteorologicalProduct(
        "Compact daylight day-of-year lookup",
        "daylight",
        {
            4: "data/processed/domain/environmental_layer/meteorological/daylight/"
            "H3_DAYLIGHT_DOY_RES_4.parquet"
        },
        "meteorology.daylight.build",
        "one row per H3 cell and day of year",
        dependencies=("daylight_daily",),
        index_columns=("H3_INDEX", "DAY_OF_YEAR"),
    ),
    "lunar_daily": MeteorologicalProduct(
        "Daily lunar phase and moonlight exposure",
        "lunar",
        {
            5: "data/processed/domain/environmental_layer/meteorological/lunar/"
            "H3_LUNAR_DAILY_RES_5"
        },
        "meteorology.lunar.build",
        "one row per H3 cell and local date",
        ("YEAR",),
        ("meteorological_spatial_support",),
    ),
    "hrrr_nearest_grid_crosswalk": MeteorologicalProduct(
        "H3 to HRRR nearest-grid crosswalk",
        "spatial_support",
        {
            5: "data/processed/domain/environmental_layer/meteorological/surface_weather/"
            "H3_HRRR_NEAREST_GRID_RES_5"
        },
        "meteorology.surface_weather.build",
        "one row per H3 cell and HRRR source-grid hash",
        ("SOURCE_GRID_HASH",),
        ("meteorological_spatial_support",),
        index_columns=("H3_INDEX", "SOURCE_GRID_HASH"),
    ),
    "hrrr_source_inventory": MeteorologicalProduct(
        "HRRR acquisition inventory",
        "source_inventory",
        {
            5: "data/raw/environment/meteorological/surface_weather/hrrr/"
            "HRRR_R5_SOURCE_INVENTORY.parquet"
        },
        "meteorology.surface_weather.download",
        "one row per expected HRRR valid time",
        index_columns=("VALID_TIME_UTC",),
    ),
}


def _materialized_schema(path: Path) -> pa.Schema:
    files = [path] if path.is_file() else sorted(path.rglob("*.parquet"))
    if not files:
        raise FileNotFoundError(f"No Parquet artifacts found for catalog product: {path}")
    schema = pq.read_schema(files[0])
    for file in files[1:]:
        observed = pq.read_schema(file)
        if not observed.equals(schema, check_metadata=False):
            raise ValueError(f"Meteorological partition schema drift: {file}")
    return schema


def _role(column: str, product: MeteorologicalProduct, field: pa.Field) -> str:
    upper = column.upper()
    if product.subfamily in {"source_inventory", "spatial_support"}:
        return "provenance_or_qc"
    if upper in KEY_COLUMNS or upper.endswith("_INDEX") or upper.endswith("_ID"):
        return "identifier"
    if any(token in upper for token in ("COVERAGE", "SAMPLE_COUNT", "EXPECTED_SAMPLE")):
        return "coverage"
    if any(
        token in upper
        for token in (
            "QC",
            "STATE",
            "CHECKSUM",
            "HASH",
            "SOURCE_",
            "AVAILABLE_AT",
            "VALID_TIME",
            "INIT_TIME",
            "RELATIVE_PATH",
            "FAILURE_REASON",
            "VARIABLE_COUNT",
            "FORECAST_HOUR",
        )
    ):
        return "provenance_or_qc"
    if (
        upper
        in {
            "YEAR",
            "DAY_OF_YEAR",
            "MONTH",
            "DAY",
            "MONTH_DAY",
            "IS_LEAP_DAY",
            "SOLAR_DAY_365",
            "CENTROID_LAT",
            "CENTROID_LON",
            "H3_RESOLUTION",
        }
        or "DISTANCE_M" in upper
    ):
        return "bookkeeping"
    if product.subfamily in {"daylight", "lunar"}:
        return "astronomical_metric"
    if pa.types.is_floating(field.type) or pa.types.is_integer(field.type):
        return "physical_metric"
    return "categorical_metric"


def _unit(column: str) -> str:
    upper = column.upper()
    if upper in {"CENTROID_LAT", "CENTROID_LON"}:
        return "decimal degrees"
    if upper.endswith("_UTC") or "_TIME_UTC" in upper:
        return "UTC timestamp"
    for suffix, unit in (
        ("_C", "degrees Celsius"),
        ("_HPA", "hectopascals"),
        ("_MM_HR", "millimetres per hour"),
        ("_MM_DAY", "millimetres"),
        ("_KM", "kilometres"),
        ("_MS", "metres per second"),
        ("_M", "metres"),
        ("_DEG", "degrees"),
        ("_HOURS", "hours"),
        ("_DAYS", "days"),
        ("_PCT", "percent"),
        ("_FRAC", "fraction [0,1]"),
        ("_FRACTION", "fraction [0,1]"),
        ("_SCORE", "unitless [0,1]"),
        ("_INDEX", "unitless index"),
    ):
        if upper.endswith(suffix) or f"{suffix}_" in upper:
            return unit
    return "categorical" if upper.endswith(("STATE", "NAME", "REASON")) else "unitless"


def _declared_schemas() -> dict[str, pa.Schema]:
    from meteorology.daylight.build import (
        DAYLIGHT_DOY_SCHEMA,
        DAYLIGHT_SCHEMA,
    )
    from meteorology.lunar.build import LUNAR_SCHEMA
    from meteorology.spatial_support.build import SUPPORT_SCHEMA
    from meteorology.surface_weather.build import DAILY_SCHEMA
    from meteorology.surface_weather.download import (
        INVENTORY_SCHEMA,
    )
    from meteorology.surface_weather.sampling import (
        CROSSWALK_SCHEMA,
    )

    return {
        "meteorological_spatial_support": SUPPORT_SCHEMA,
        "surface_weather_daily": DAILY_SCHEMA,
        "daylight_daily": DAYLIGHT_SCHEMA,
        "daylight_day_of_year": DAYLIGHT_DOY_SCHEMA,
        "lunar_daily": LUNAR_SCHEMA,
        "hrrr_nearest_grid_crosswalk": CROSSWALK_SCHEMA,
        "hrrr_source_inventory": INVENTORY_SCHEMA,
    }


def _meteorological_products(
    root: Path,
) -> tuple[dict[str, Any], int, set[str], list[str]]:
    products: dict[str, Any] = {}
    entry_count = 0
    unique: set[str] = set()
    pending_products: list[str] = []
    declared_schemas = _declared_schemas()
    feature_roles = {
        "physical_metric",
        "astronomical_metric",
        "storm_metric",
        "categorical_metric",
    }
    for product_id, spec in METEOROLOGICAL_PRODUCTS.items():
        schemas: dict[int, pa.Schema] = {}
        paths: dict[int, str] = {}
        materialized: dict[int, bool] = {}
        for resolution, relative in sorted(spec.paths.items()):
            path = (root / relative).resolve()
            materialized[resolution] = path.exists()
            observed = (
                _materialized_schema(path)
                if materialized[resolution]
                else declared_schemas[product_id]
            )
            if not observed.equals(declared_schemas[product_id], check_metadata=False):
                raise ValueError(
                    f"Meteorological artifact disagrees with declared producer schema: {path}"
                )
            schemas[resolution] = observed
            paths[resolution] = str(path.relative_to(root))
        if not all(materialized.values()):
            pending_products.append(product_id)
        columns = sorted(
            {
                column
                for schema in schemas.values()
                for column in schema.names
                if column not in KEY_COLUMNS
            }
        )
        fields: dict[str, Any] = {}
        for column in columns:
            available = [r for r, schema in schemas.items() if column in schema.names]
            field = schemas[available[0]].field(column)
            role = _role(column, spec, field)
            variable_kind = "feature_variable" if role in feature_roles else "metadata"
            fields[column] = {
                "common_name": column.replace("_", " ").title(),
                "metric_family": "meteorological",
                "metric_subfamily": spec.subfamily,
                "column": column,
                "collection_paths": {r: paths[r] for r in available},
                "unit": _unit(column),
                "arrow_type": str(field.type),
                "role": role,
                "variable_kind": variable_kind,
                "topology": "within_cell_or_nonspatial",
                "scale_group": column,
                "available_resolutions": available,
                "modeling_use": (
                    "eligible_after_policy_review"
                    if variable_kind == "feature_variable"
                    else "excluded_metadata"
                ),
                "default_ecological_occurrence_model": None,
            }
            entry_count += 1
            unique.add(column)
        products[product_id] = {
            "common_name": spec.common_name,
            "metric_family": "meteorological",
            "metric_subfamily": spec.subfamily,
            "category": spec.subfamily,
            "collection": {
                "paths": paths,
                "index_columns": list(spec.index_columns),
                "partition_keys": list(spec.partition_keys),
                "resolutions": sorted(paths),
                "materialized": materialized,
                "materialization_state": (
                    "complete" if all(materialized.values()) else "pending_acquisition_or_build"
                ),
                "schema_source": (
                    "materialized_partitions"
                    if all(materialized.values())
                    else "declared_producer_schema_pending_materialization"
                ),
            },
            "producer": spec.producer,
            "dependencies": list(spec.dependencies),
            "grain": spec.grain,
            "feature_count": len(fields),
            "variable_kind_counts": {
                kind: sum(field["variable_kind"] == kind for field in fields.values())
                for kind in ("feature_variable", "metadata")
            },
            "features": fields,
        }
    return products, entry_count, unique, pending_products


def build_catalog(root: Path) -> dict[str, Any]:
    base = {
        "products": {}, "feature_entry_count": 0,
        "supporting_products": {}, "superseded_products": {},
        "catalog_contract": {
            "collection_lookup": "Collection paths identify Parquet products; column identifies the field.",
            "common_name_lookup": "Use products.<product>.features.<column>.common_name.",
            "metric_family_vocabulary": ["meteorological"],
            "roles": {},
            "metric_subfamily_vocabulary": [],
            "variable_kinds": {
                "feature_variable": "Substantive physical or astronomical metric.",
                "metadata": "Coverage, provenance, QC, support, identity or bookkeeping.",
            },
        },
    }
    meteorological, entry_count, unique, pending_products = _meteorological_products(root)
    overlap = sorted(set(base["products"]).intersection(meteorological))
    if overlap:
        raise ValueError(f"Environment catalog product IDs overlap: {overlap}")
    base["schema_version"] = 4
    base["catalog_id"] = "environment"
    base["catalog_contract"]["scope"] = (
        "Every non-key column in the canonical materialized meteorological "
        "products listed below; all meteorological partitions must share one Arrow schema."
    )
    base["catalog_contract"]["metric_taxonomy"] = (
        "metric_family identifies the environment family; metric_subfamily groups mechanisms "
        "within meteorological products."
    )
    base["catalog_contract"]["excluded_key_columns"] = sorted(KEY_COLUMNS | {"H3_RESOLUTION"})
    base["catalog_contract"]["roles"].update(
        {
            "physical_metric": "Measured or physically derived atmospheric metric.",
            "astronomical_metric": "Deterministic solar or lunar metric.",
            "storm_metric": "Operational storm metric, not an authoritative classification.",
            "categorical_metric": "Substantive categorical environmental value.",
            "provenance_or_qc": "Source lineage, availability, support, or QC metadata.",
            "bookkeeping": "Calendar, coordinate, distance, or deterministic bookkeeping metadata.",
        }
    )
    base["catalog_contract"]["materialization_policy"] = (
        "Materialized meteorological products are generated from and checked across every Parquet "
        "partition. Products awaiting acquisition/build remain cataloged from the fixed producer "
        "schema and are explicitly marked pending; release requires an empty pending list."
    )
    base["catalog_contract"]["metric_subfamily_vocabulary"] = sorted(
        set(base["catalog_contract"]["metric_subfamily_vocabulary"])
        | {spec.subfamily for spec in METEOROLOGICAL_PRODUCTS.values()}
    )
    base["products"].update(meteorological)
    base["product_count"] = len(base["products"])
    base["feature_entry_count"] += entry_count
    base["unique_column_count"] = len(unique)
    base["family_counts"] = {"meteorological": len(meteorological)}
    base["generated_from_materialized_schemas"] = not pending_products
    base["pending_materialization_products"] = sorted(pending_products)
    return base


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_PATH))
    parser.add_argument(
        "--materialization-root",
        help="Project-mirrored root containing the artifacts to inspect.",
    )
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    root = project_root()
    destination = (root / args.output).resolve()
    materialization_root = (
        Path(args.materialization_root).resolve() if args.materialization_root else root
    )
    snapshot = nullcontext()
    with snapshot:
        catalog = build_catalog(materialization_root)
    rendered = yaml.safe_dump(catalog, sort_keys=False, allow_unicode=True, width=100)
    if args.check:
        if not destination.exists() or destination.read_text(encoding="utf-8") != rendered:
            raise SystemExit(
                f"Environment feature catalog is stale; regenerate with {Path(__file__).name}."
            )
        print(f"Environment feature catalog is current: {destination}")
        return 0
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")
    try:
        partial.write_text(rendered, encoding="utf-8")
        partial.replace(destination)
    finally:
        partial.unlink(missing_ok=True)
    print(
        f"Wrote {catalog['feature_entry_count']} entries across "
        f"{catalog['product_count']} environment products -> {destination}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
