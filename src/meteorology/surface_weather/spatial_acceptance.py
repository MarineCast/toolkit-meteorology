"""Versioned geographic eligibility for exact-support HRRR point samples."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

import numpy as np
import pandas as pd
from pyproj import Geod
from shapely.geometry import Point, Polygon
from sklearn.neighbors import BallTree

from ..artifacts import stable_hash

POLICY_ID = "hrrr_conus_native_nearest_v1"
SYNTHETIC_POLICY_ID = "synthetic_native_nearest_v1"
GRID_SPEC_URL = "https://www.emc.ncep.noaa.gov/mmb/namgrids/hrrrspecs.html"
EARTH_RADIUS_M = 6_371_008.8
GEODESIC_TOLERANCE_M = 10.0
GEOD = Geod(ellps="WGS84")


@dataclass(frozen=True)
class NativeGrid:
    """Full decoded source coordinates, never the requested or extracted crop."""

    lat: np.ndarray
    lon: np.ndarray
    checksum: str

    @classmethod
    def from_coordinates(cls, lat: np.ndarray, lon: np.ndarray) -> NativeGrid:
        latitude = np.ascontiguousarray(lat, dtype="<f8")
        longitude = np.ascontiguousarray(lon, dtype="<f8")
        if (
            latitude.ndim != 2 or latitude.shape != longitude.shape
            or min(latitude.shape) < 2 or not np.isfinite(latitude).all()
            or not np.isfinite(longitude).all()
        ):
            raise ValueError("Full native HRRR grid requires finite 2-D coordinates.")
        digest = hashlib.sha256()
        digest.update(json.dumps(latitude.shape, separators=(",", ":")).encode())
        digest.update(latitude.tobytes(order="C"))
        digest.update(longitude.tobytes(order="C"))
        return cls(latitude, longitude, digest.hexdigest())

    def validate_identity(self, policy_id: str) -> None:
        if policy_id == SYNTHETIC_POLICY_ID:
            return
        if policy_id != POLICY_ID:
            raise ValueError(f"Unknown HRRR spatial acceptance policy: {policy_id}")
        if self.lat.shape != (1059, 1799):
            raise ValueError(
                f"Decoded native HRRR shape {self.lat.shape} differs from NOAA CONUS "
                "3-km grid (1059, 1799)."
            )
        for j, i, expected_lat, expected_lon in (
            (0, 0, 21.138, -122.720),
            (1058, 1798, 47.842, -60.917),
        ):
            _, _, error_m = GEOD.inv(
                expected_lon, expected_lat, float(self.lon[j, i]), float(self.lat[j, i])
            )
            # NOAA publishes corners to 0.001 degree; 250 m allows that
            # rounding but rejects a different domain/projection.
            if error_m > 250.0:
                raise ValueError(
                    f"Decoded native HRRR corner ({j},{i}) differs from NOAA grid "
                    f"specification by {error_m:.1f} m."
                )
        for j, i in ((0, 0), (529, 899)):
            _, _, dx = GEOD.inv(
                self.lon[j, i], self.lat[j, i], self.lon[j, i + 1], self.lat[j, i + 1]
            )
            _, _, dy = GEOD.inv(
                self.lon[j, i], self.lat[j, i], self.lon[j + 1, i], self.lat[j + 1, i]
            )
            if not (2500 <= dx <= 3500 and 2500 <= dy <= 3500):
                raise ValueError("Decoded HRRR grid spacing differs from NOAA's 3-km grid.")

    def footprint(self) -> Polygon:
        lat, lon = self.lat, self.lon
        edge = list(zip(lon[0, :], lat[0, :], strict=True))
        edge += list(zip(lon[1:, -1], lat[1:, -1], strict=True))
        edge += list(zip(lon[-1, -2::-1], lat[-1, -2::-1], strict=True))
        edge += list(zip(lon[-2:0:-1, 0], lat[-2:0:-1, 0], strict=True))
        polygon = Polygon(edge)
        if not polygon.is_valid or polygon.is_empty:
            raise ValueError("Decoded native HRRR footprint is invalid.")
        return polygon

    def half_local_diagonal_m(self, flat_index: int) -> float:
        ny, nx = self.lat.shape
        j, i = divmod(int(flat_index), nx)
        diagonals: list[float] = []
        for y in (j - 1, j):
            for x in (i - 1, i):
                if 0 <= y < ny - 1 and 0 <= x < nx - 1:
                    for first, second in (((y, x), (y + 1, x + 1)),
                                          ((y + 1, x), (y, x + 1))):
                        _, _, distance = GEOD.inv(
                            self.lon[first], self.lat[first],
                            self.lon[second], self.lat[second],
                        )
                        diagonals.append(float(distance))
        if not diagonals:
            raise ValueError("Native HRRR node has no adjacent grid cell.")
        return max(diagonals) / 2.0 + GEODESIC_TOLERANCE_M


class SpatialAcceptanceError(ValueError):
    """Exact-support sampling rejected one or more targets with diagnostics."""

    def __init__(self, report: dict[str, object]):
        self.report = report
        rejected = report["rejected"]
        preview = ", ".join(
            f"{item['h3_index']}:{item['reason']}" for item in rejected[:5]
        )
        super().__init__(
            f"HRRR spatial acceptance rejected {len(rejected)} of "
            f"{report['target_count']} support cells ({preview}); "
            "see run spatial_rejections JSON for every cell."
        )


def support_identity(support: pd.DataFrame) -> str:
    return stable_hash(
        sorted(
            (str(row.H3_INDEX), round(float(row.CENTROID_LAT), 7),
             round(float(row.CENTROID_LON), 7))
            for row in support.itertuples(index=False)
        )
    )


def validate_spatial_acceptance(
    support: pd.DataFrame, source_grid: pd.DataFrame, *, policy_id: str = POLICY_ID
) -> dict[str, object]:
    """Reject outside targets, absent native nodes and nonrepresentative samples.

    Full-grid and crop nearest queries both use the same haversine BallTree as
    crosswalk construction. WGS84 geodesic distances are used for the local
    half-cell-diagonal representativeness limit and its 10 m tolerance.
    """

    native = source_grid.attrs.get("native_grid")
    native_indices = source_grid.attrs.get("native_indices")
    if not isinstance(native, NativeGrid) or native_indices is None:
        raise ValueError("HRRR native-grid coordinates/indices are missing; cannot establish spatial acceptance.")
    native.validate_identity(policy_id)
    native_indices = np.asarray(native_indices, dtype=np.int64)
    if len(native_indices) != len(source_grid) or len(np.unique(native_indices)) != len(native_indices):
        raise ValueError("HRRR crop has missing or duplicate native-grid row identities.")
    native_lat = native.lat.ravel(order="C")
    native_lon = native.lon.ravel(order="C")
    if (
        (native_indices < 0).any() or (native_indices >= native_lat.size).any()
        or not np.allclose(source_grid["SOURCE_LAT"], native_lat[native_indices], rtol=0, atol=1e-7)
        or not np.allclose(source_grid["SOURCE_LON"], native_lon[native_indices], rtol=0, atol=1e-7)
    ):
        raise ValueError("HRRR crop coordinates do not match native-grid row identities.")
    points = support[["CENTROID_LAT", "CENTROID_LON"]].to_numpy(dtype=float)
    if not np.isfinite(points).all() or support["H3_INDEX"].duplicated().any():
        raise ValueError("Meteorological support contains invalid coordinates or duplicate cells.")
    native_tree = BallTree(np.deg2rad(np.column_stack((native_lat, native_lon))), metric="haversine")
    native_distance, native_nearest = native_tree.query(np.deg2rad(points), k=1)
    native_nearest = native_nearest[:, 0]
    native_distance = native_distance[:, 0] * EARTH_RADIUS_M
    crop_tree = BallTree(
        np.deg2rad(source_grid[["SOURCE_LAT", "SOURCE_LON"]].to_numpy(dtype=float)),
        metric="haversine",
    )
    crop_distance, crop_nearest = crop_tree.query(np.deg2rad(points), k=1)
    crop_nearest = crop_nearest[:, 0]
    crop_distance = crop_distance[:, 0] * EARTH_RADIUS_M
    crop_native = native_indices[crop_nearest]
    footprint = native.footprint()
    rejected: list[dict[str, object]] = []
    limits: list[float] = []
    for n, row in enumerate(support.itertuples(index=False)):
        nearest = int(native_nearest[n])
        limit = native.half_local_diagonal_m(nearest)
        limits.append(limit)
        if not footprint.covers(Point(float(row.CENTROID_LON), float(row.CENTROID_LAT))):
            reason = "outside_native_footprint"
        elif native_distance[n] > limit:
            reason = "native_point_too_distant"
        elif crop_native[n] != nearest and crop_distance[n] > native_distance[n] + 0.01:
            reason = "native_nearest_missing_from_crop"
        elif crop_distance[n] > limit:
            reason = "cropped_point_too_distant"
        else:
            continue
        rejected.append({
            "h3_index": str(row.H3_INDEX), "reason": reason,
            "target_lat": float(row.CENTROID_LAT), "target_lon": float(row.CENTROID_LON),
            "native_nearest_index": nearest,
            "native_distance_m": round(float(native_distance[n]), 3),
            "crop_distance_m": round(float(crop_distance[n]), 3),
            "local_limit_m": round(limit, 3),
        })
    report: dict[str, object] = {
        "policy_id": policy_id, "native_grid_checksum": native.checksum,
        "native_grid_shape": list(native.lat.shape),
        "native_grid_spec_url": GRID_SPEC_URL if policy_id == POLICY_ID else None,
        "support_hash": support_identity(support),
        "source_grid_hash": str(source_grid["SOURCE_GRID_HASH"].iloc[0]),
        "target_count": len(support), "rejected": rejected,
        "max_native_distance_m": float(np.max(native_distance)),
        "max_crop_distance_m": float(np.max(crop_distance)),
        "max_local_limit_m": max(limits),
    }
    if rejected:
        raise SpatialAcceptanceError(report)
    return report
