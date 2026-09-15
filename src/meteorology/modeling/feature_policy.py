"""Generate and apply the meteorological model-feature policy."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from meteorology.core.config.paths import project_root

DEFAULT_CATALOG_PATH = Path("config/feature_catalog.yaml")
DEFAULT_POLICY_PATH = Path(
    "config/model_feature_policy.yaml"
)

# These columns are exactly determined by another retained field under the
# published formula.  They remain in scientific artifacts but not in the
# default model matrix.
DETERMINISTIC_ALIASES = {
    ("daylight_daily", "DAYLIGHT_FRACTION"): "deterministic_transform_of_daylight_hours",
    ("daylight_daily", "DAYLIGHT_WEIGHT"): "duplicate_of_daylight_fraction",
    ("daylight_day_of_year", "DAYLIGHT_FRACTION"): ("deterministic_transform_of_daylight_hours"),
    ("daylight_day_of_year", "WEIGHT_DAYLIGHT"): "scaled_alias_of_daylight_weight",
    ("lunar_daily", "LUNAR_PHASE_ANGLE_DEG"): "deterministic_transform_of_lunar_age_days",
    ("lunar_daily", "WEIGHT_LUNAR_ILLUMINATION"): ("duplicate_of_lunar_illumination_fraction"),
    ("lunar_daily", "WEIGHT_MOONLIT_DARK_HOURS"): ("duplicate_of_moonlit_dark_fraction"),
}


def meteorological_catalog_subset(catalog: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    """Return a family-scoped catalog identity unaffected by seascape changes."""

    products = catalog.get("products")
    if not isinstance(products, Mapping):
        raise ValueError("Environment feature catalog has no products mapping.")
    subset = {
        "schema_version": catalog.get("schema_version"),
        "catalog_id": "environment.meteorological.subset",
        "products": {
            str(product_id): product
            for product_id, product in products.items()
            if isinstance(product, Mapping) and product.get("metric_family") == "meteorological"
        },
    }
    encoded = json.dumps(subset, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return subset, hashlib.sha256(encoded).hexdigest()


def _exclusion_reason(product: str, field: Mapping[str, Any], column: str) -> str | None:
    variable_kind = str(field.get("variable_kind", "metadata"))
    if variable_kind != "feature_variable":
        return f"catalog_variable_kind:{variable_kind}"
    modeling_use = str(field.get("modeling_use", "eligible_after_policy_review"))
    if modeling_use == "observer_effort_only":
        return "observer_effort_only_not_ecological_occurrence"
    if product == "daylight_day_of_year":
        return "alternate_lookup_duplicate_of_daily_product"
    return DETERMINISTIC_ALIASES.get((product, column))


def build_feature_policy(catalog: Mapping[str, Any]) -> dict[str, Any]:
    """Build a complete field-level default ecological-model policy."""

    subset, checksum = meteorological_catalog_subset(catalog)
    records: list[dict[str, Any]] = []
    for product_id, product in sorted(subset["products"].items()):
        features = product.get("features", {})
        if not isinstance(features, Mapping):
            raise ValueError(f"Catalog product has no feature mapping: {product_id}")
        for column, field in sorted(features.items()):
            if not isinstance(field, Mapping):
                raise ValueError(f"Invalid catalog field: {product_id}.{column}")
            reason = _exclusion_reason(str(product_id), field, str(column))
            records.append(
                {
                    "product": str(product_id),
                    "column": str(column),
                    "role": field.get("role"),
                    "variable_kind": field.get("variable_kind"),
                    "feature_family": "meteorological",
                    "metric_subfamily": field.get("metric_subfamily"),
                    "scale_group": field.get("scale_group", f"{product_id}:{column}"),
                    "topology": field.get("topology", "within_cell_or_nonspatial"),
                    "modeling_use": field.get("modeling_use"),
                    "included_by_default": reason is None,
                    "exclusion_reason": reason,
                }
            )
    return {
        "schema_version": 1,
        "policy_scope": "default_ecological_occurrence_model",
        "feature_catalog_subset_checksum": checksum,
        "complete_scientific_products_retained": True,
        "deterministic_duplicate_contract": {
            "DAYLIGHT_FRACTION": "DAYLIGHT_HOURS / 24",
            "DAYLIGHT_WEIGHT": "DAYLIGHT_FRACTION under the configured default",
            "LUNAR_PHASE_ANGLE_DEG": "360 * LUNAR_AGE_DAYS / 29.530588853 modulo 360",
            "WEIGHT_LUNAR_ILLUMINATION": "LUNAR_ILLUMINATION_FRACTION",
            "WEIGHT_MOONLIT_DARK_HOURS": "MOONLIT_DARK_FRACTION",
        },
        "features": records,
    }


def load_feature_policy(
    path: str | Path = DEFAULT_POLICY_PATH,
    *,
    catalog_path: str | Path = DEFAULT_CATALOG_PATH,
    verify_catalog: bool = True,
) -> dict[str, Any]:
    root = project_root()
    policy_path = Path(path)
    policy_path = policy_path if policy_path.is_absolute() else root / policy_path
    payload = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("features"), list):
        raise ValueError(f"Invalid meteorological model-feature policy: {policy_path}")
    if verify_catalog:
        catalog_file = Path(catalog_path)
        catalog_file = catalog_file if catalog_file.is_absolute() else root / catalog_file
        catalog = yaml.safe_load(catalog_file.read_text(encoding="utf-8"))
        _, checksum = meteorological_catalog_subset(catalog)
        if payload.get("feature_catalog_subset_checksum") != checksum:
            raise ValueError("Meteorological model-feature policy is stale for the catalog.")
    return payload


def apply_feature_policy(frame: pd.DataFrame, policy: Mapping[str, Any]) -> pd.DataFrame:
    """Return keys plus default-included fields, failing on missing selected fields."""

    records = policy.get("features")
    if not isinstance(records, Sequence):
        raise ValueError("Meteorological model-feature policy has no feature records.")
    selected: list[str] = []
    missing: list[str] = []
    for record in records:
        if not isinstance(record, Mapping) or not record.get("included_by_default"):
            continue
        column = str(record["column"])
        prefixed = f"{record['product']}__{column}"
        if prefixed in frame.columns:
            selected.append(prefixed)
        elif column in frame.columns:
            selected.append(column)
        else:
            missing.append(f"{record['product']}.{column}")
    missing = sorted(missing)
    if missing:
        raise ValueError(f"Model matrix is missing policy-selected fields: {missing[:10]}")
    keys = [column for column in ("H3_INDEX", "DATE") if column in frame.columns]
    return frame[[*keys, *sorted(set(selected))]].copy()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", default=str(DEFAULT_CATALOG_PATH))
    parser.add_argument("--output", default=str(DEFAULT_POLICY_PATH))
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    root = project_root()
    catalog_path = Path(args.catalog)
    catalog_path = catalog_path if catalog_path.is_absolute() else root / catalog_path
    output_path = Path(args.output)
    output_path = output_path if output_path.is_absolute() else root / output_path
    catalog = yaml.safe_load(catalog_path.read_text(encoding="utf-8"))
    rendered = yaml.safe_dump(
        build_feature_policy(catalog), sort_keys=False, allow_unicode=True, width=100
    )
    if args.check:
        if not output_path.exists() or output_path.read_text(encoding="utf-8") != rendered:
            raise SystemExit("Meteorological model-feature policy is stale.")
        print(f"Meteorological model-feature policy is current: {output_path}")
        return 0
    output_path.parent.mkdir(parents=True, exist_ok=True)
    partial = output_path.with_suffix(output_path.suffix + ".part")
    try:
        partial.write_text(rendered, encoding="utf-8")
        partial.replace(output_path)
    finally:
        partial.unlink(missing_ok=True)
    print(f"Wrote meteorological model-feature policy: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
