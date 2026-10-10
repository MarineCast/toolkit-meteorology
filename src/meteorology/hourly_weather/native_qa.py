"""Independent native HRRR unit, UTC-slot, wind, count and daily QA."""

from __future__ import annotations

import math

import pandas as pd

from .native_source import METRIC_INPUTS


def _normalize(value, name, units):
    # Independent scalar arithmetic; do not call the producer's normalization.
    unit = units.lower().replace(" ", "")
    if name == "temperature_2m_k":
        if unit in ("k", "kelvin"):
            return value
        if unit in ("c", "degc", "°c", "celsius"):
            return value + 273.15
    if name in ("relative_humidity_2m_pct", "total_cloud_cover_pct"):
        if unit in ("%", "percent", "pct"):
            return value
        if unit in ("1", "fraction", "proportion"):
            return value * 100
    if name in ("u_wind_10m_ms", "v_wind_10m_ms", "wind_gust_surface_ms") and unit in (
        "m/s",
        "ms-1",
        "ms**-1",
        "m.s-1",
    ):
        return value
    if name == "visibility_m":
        if unit in ("m", "meter", "metre", "meters", "metres"):
            return value
        if unit in ("km", "kilometer", "kilometre", "kilometers", "kilometres"):
            return value * 1000
    if name == "mean_sea_level_pressure_pa":
        if unit in ("pa", "pascal", "pascals"):
            return value
        if unit in ("hpa", "mb", "mbar"):
            return value * 100
    raise ValueError("Independent QA rejected source unit metadata.")


def _same(actual, expected):
    return (pd.isna(actual) and not math.isfinite(expected)) or (
        math.isfinite(expected)
        and pd.notna(actual)
        and math.isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-9)
    )


def independent_qa(hourly, daily, plan, hours, *, budget):
    start = pd.Timestamp(plan["day"], tz="UTC")
    schedule = {start + pd.Timedelta(hours=h) for h in range(24)}
    actual = {pd.Timestamp(value) for value in hourly.VALID_TIME_UTC}
    if (
        not actual <= schedule
        or hourly.duplicated(["SOURCE_GRID_INDEX", "VALID_TIME_UTC"]).any()
        or set(daily.DATE) != {plan["day"]}
        or set(daily.TIMEZONE) != {"UTC"}
        or set(hourly.SOURCE_MODEL) != {"HRRR"}
        or set(daily.SOURCE_MODEL) != {"HRRR"}
        or daily.SOURCE_GRID_INDEX.duplicated().any()
    ):
        raise ValueError("Independent UTC/day/source/key check failed.")
    evidence = {pd.Timestamp(hour["valid_time_utc"]): hour["decoder"] for hour in hours}
    if set(evidence) != actual or len(hours) != len(actual):
        raise ValueError("Hour provenance does not match actual native hours.")
    metrics = tuple(METRIC_INPUTS) + ("WIND_SPEED_10M_MS",)
    checked = complete = partial = missing = 0
    budget.checkpoint(additional_memory=len(hourly) * 512 * 4)
    for row in hourly.itertuples(index=False):
        if row.INIT_TIME_UTC != row.VALID_TIME_UTC or row.FORECAST_HOUR != 0:
            raise ValueError("Independent initial/valid/lead check failed.")
        source = evidence[pd.Timestamp(row.VALID_TIME_UTC)]
        normalized = {
            name: _normalize(getattr(row, "RAW_SOURCE_" + name), name, source["units"][name])
            for name, _ in METRIC_INPUTS.values()
        }
        raw_u, raw_v = normalized["u_wind_10m_ms"], normalized["v_wind_10m_ms"]
        if source["source_wind_basis"] == "grid_relative":
            angle = math.radians(row.SOURCE_I_BEARING_DEG)
            hand = row.SOURCE_J_HANDEDNESS
            u = raw_u * math.sin(angle) + hand * raw_v * math.cos(angle)
            v = raw_u * math.cos(angle) - hand * raw_v * math.sin(angle)
        elif source["source_wind_basis"] == "earth_relative":
            u, v = raw_u, raw_v
        else:
            raise ValueError("Unsupported source wind basis in independent QA.")
        normalized["u_wind_10m_ms"] = u
        normalized["v_wind_10m_ms"] = v
        for metric, (name, _) in METRIC_INPUTS.items():
            expected = normalized[name]
            if metric == "TEMPERATURE_2M_C":
                expected -= 273.15
            if metric == "VISIBILITY_KM":
                expected /= 1000
            if metric == "MEAN_SEA_LEVEL_PRESSURE_HPA":
                expected /= 100
            if not _same(getattr(row, metric), expected):
                raise ValueError("Independent raw-to-hourly unit/wind conversion disagrees.")
        if not _same(row.WIND_SPEED_10M_MS, math.hypot(raw_u, raw_v)):
            raise ValueError("Independent hourly wind magnitude disagrees.")
    for n, (index, group) in enumerate(hourly.groupby("SOURCE_GRID_INDEX")):
        if n % 64 == 0:
            budget.checkpoint()
        if set(pd.to_datetime(group.VALID_TIME_UTC, utc=True)) != actual:
            raise ValueError("Hour/native-point universe is incomplete.")
        match = daily[daily.SOURCE_GRID_INDEX == index]
        if len(match) != 1:
            raise ValueError("Native daily point is absent or duplicated.")
        out = match.iloc[0]
        for metric in metrics:
            values = [
                float(value) for value in group[metric] if pd.notna(value) and math.isfinite(value)
            ]
            count = len(values)
            prefix = "HRRR_" + metric
            status = "COMPLETE" if count == 24 else "PARTIAL" if count else "UNAVAILABLE"
            if (
                out[prefix + "_VALID_HOURS"] != count
                or out[prefix + "_EXPECTED_HOURS"] != 24
                or out[prefix + "_STATUS"] != status
                or not math.isclose(out[prefix + "_COVERAGE_FRACTION"], count / 24, abs_tol=1e-12)
            ):
                raise ValueError("Independent count/coverage/status check failed.")
            if count == 24:
                if not _same(out[prefix], math.fsum(values) / 24):
                    raise ValueError("Independent hourly-to-daily mean disagrees.")
                complete += 1
            else:
                if pd.notna(out[prefix]):
                    raise ValueError("Incomplete daily metric must remain null.")
                partial += 1
            checked += 1
            missing += 24 - count
        for metric in ("DEWPOINT_2M_C", "PRECIPITATION_MM"):
            prefix = "HRRR_" + metric
            if (
                pd.notna(out[prefix])
                or out[prefix + "_STATUS"] != "UNAVAILABLE"
                or out[prefix + "_VALID_HOURS"] != 0
                or out[prefix + "_EXPECTED_HOURS"] != 24
                or out[prefix + "_COVERAGE_FRACTION"] != 0
            ):
                raise ValueError("Unsupported core metric acquired a value or coverage.")
    if len(daily) != hourly.SOURCE_GRID_INDEX.nunique():
        raise ValueError("Native daily cardinality differs.")
    return dict(
        status="INDEPENDENT_NATIVE_QA_PASSED",
        actual_utc_hours=len(actual),
        missing_utc_hours=sorted(t.isoformat() for t in schedule - actual),
        native_points=len(daily),
        checked_metric_rows=checked,
        complete_metric_rows=complete,
        partial_or_unavailable_metric_rows=partial,
        missing_metric_samples=missing,
        unsupported_core_metrics=["DEWPOINT_2M_C", "PRECIPITATION_MM"],
        mean_hourly_wind_magnitude_verified=True,
        source_units_verified=True,
        final_h3_eligible=False,
        period_complete=False,
    )
