"""Parameter/time/revision evidence retained until metrics and slots are selected."""

import json
import numpy as np

ERA5 = {
    "TEMPERATURE_2M_C": ("t2m",),
    "DEWPOINT_2M_C": ("d2m",),
    "U_WIND_10M_MS": ("u10",),
    "V_WIND_10M_MS": ("v10",),
    "WIND_SPEED_10M_MS": ("u10", "v10"),
    "RELATIVE_HUMIDITY_2M_PCT": ("t2m", "d2m"),
    "MEAN_SEA_LEVEL_PRESSURE_HPA": ("msl",),
    "TOTAL_CLOUD_COVER_PCT": ("tcc",),
    "PRECIPITATION_MM": ("tp",),
    "SNOWFALL_WATER_EQUIVALENT_MM": ("sf",),
}
HRRR = {
    "TEMPERATURE_2M_C": ("temperature_2m_k",),
    # Earth-relative components use both raw grid-relative components for rotation.
    "U_WIND_10M_MS": ("u_wind_10m_ms", "v_wind_10m_ms"),
    "V_WIND_10M_MS": ("u_wind_10m_ms", "v_wind_10m_ms"),
    "WIND_SPEED_10M_MS": ("u_wind_10m_ms", "v_wind_10m_ms"),
    "RELATIVE_HUMIDITY_2M_PCT": ("relative_humidity_2m_pct",),
    "MEAN_SEA_LEVEL_PRESSURE_HPA": ("mean_sea_level_pressure_pa",),
    "TOTAL_CLOUD_COVER_PCT": ("total_cloud_cover_pct",),
    "VISIBILITY_KM": ("visibility_m",),
}


def parameters(source, metric):
    return (ERA5 if source == "era5" else HRRR)[metric]


def era5_evidence(row, generation):
    return json.dumps(
        dict(
            parameter=row.PARAMETER,
            valid_time_utc=row.VALID_TIME_UTC,
            source_grid_index=int(row.SOURCE_GRID_INDEX),
            source_grid_hash=row.SOURCE_GRID_HASH,
            selected_revision=int(row.EXPVER),
            source_generation=generation,
            retrieved_at_utc=row.RETRIEVED_AT_UTC,
            source_sha256=row.SOURCE_SHA256,
            consolidation_status="contains_preliminary_ERA5T" if row.EXPVER == 5 else "final",
            value_status="VALID" if np.isfinite(row.VALUE) else "BITMAP_NULL",
            init_time_utc=row.INIT_TIME_UTC,
            forecast_hour=int(row.FORECAST_HOUR),
            interval_start_utc=row.INTERVAL_START_UTC,
        ),
        sort_keys=True,
    )


def slot_evidence(indexed, stamp, metric, source):
    result = []
    for parameter in parameters(source, metric):
        column = parameter + "__LINEAGE"
        value = indexed.at[stamp, column] if stamp in indexed.index and column in indexed else None
        if isinstance(value, str):
            result.append(json.loads(value))
        else:
            result.append(
                dict(
                    parameter=parameter,
                    valid_time_utc=stamp.isoformat(),
                    value_status="MISSING_MESSAGE",
                )
            )
    return result


def summarize(evidence):
    times = [d["retrieved_at_utc"] for d in evidence if d.get("retrieved_at_utc")]
    statuses = {d.get("consolidation_status") for d in evidence} - {None}
    values = {d["value_status"] for d in evidence}
    return dict(
        RETRIEVED_AT_UTC=max(times) if times else None,
        SOURCE_SHA256_RECEIPTS=json.dumps(
            sorted({d["source_sha256"] for d in evidence if d.get("source_sha256")})
        ),
        CONSOLIDATION_STATUS="contains_preliminary_ERA5T"
        if "contains_preliminary_ERA5T" in statuses
        else next(iter(statuses))
        if len(statuses) == 1
        else "unavailable"
        if not statuses
        else "mixed",
        SOURCE_VALUE_STATUS=next(iter(values)) if len(values) == 1 else "MIXED_INPUT_MISSING",
        SOURCE_DEPENDENCIES=json.dumps(evidence, sort_keys=True, allow_nan=False),
    )
