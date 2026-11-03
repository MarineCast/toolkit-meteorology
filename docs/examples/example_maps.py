"""Render delivered daily ERA5 layers without acquisition or product mutation.

Usage: python example_maps.py --data-dir /path/to/Data/Meteorology --output-dir maps
Requires toolkit-meteorology runtime dependencies plus matplotlib (docs only).
"""

import argparse
import datetime as dt
import json
from pathlib import Path

import h3
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.collections import PolyCollection
from matplotlib.patches import Patch
import numpy as np
import pyarrow.parquet as pq

GROUPS = {
    "temperature": [
        ("TEMPERATURE_2M_C_MEAN", "Temperature · mean"),
        ("TEMPERATURE_2M_C_MIN", "Temperature · minimum"),
        ("TEMPERATURE_2M_C_MAX", "Temperature · maximum"),
        ("DEWPOINT_2M_C_MEAN", "Dewpoint · mean"),
    ],
    "wind": [
        ("U_WIND_10M_MS_MEAN", "Eastward wind · mean"),
        ("V_WIND_10M_MS_MEAN", "Northward wind · mean"),
        ("WIND_SPEED_10M_MS_MEAN", "Wind speed · mean"),
        ("WIND_SPEED_10M_MS_MAX", "Wind speed · maximum"),
        ("WIND_DIRECTION_10M_DEG_FROM_MEAN_VECTOR", "Wind direction · mean vector"),
    ],
    "atmosphere": [
        ("RELATIVE_HUMIDITY_2M_PCT_MEAN", "Relative humidity · mean"),
        ("TOTAL_CLOUD_COVER_PCT_MEAN", "Cloud cover · mean"),
        ("MEAN_SEA_LEVEL_PRESSURE_HPA_MEAN", "Sea-level pressure · mean"),
        ("MEAN_SEA_LEVEL_PRESSURE_HPA_MIN", "Sea-level pressure · minimum"),
        ("PRECIPITATION_MM", "Precipitation · total"),
    ],
}


def read_day(path, day):
    return pq.read_table(
        path, filters=[("DATE", "=", dt.date.fromisoformat(day))]
    ).to_pandas()


def project(lat, lon):
    # Spherical equirectangular, standard parallel 49N, origin 126W/49N.
    return (
        (np.asarray(lon) + 126) * 111.195 * np.cos(np.radians(49)),
        (np.asarray(lat) - 49) * 111.195,
    )


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data-dir", type=Path, required=True)
    p.add_argument("--output-dir", type=Path, required=True)
    p.add_argument("--date", default="2026-10-19")
    p.add_argument("--partial-date", default="2026-10-20")
    a = p.parse_args()
    if a.output_dir.resolve() == a.data_dir.resolve():
        p.error("Use a separate documentation output directory.")
    catalog = json.loads((a.data_dir / "catalog.json").read_text())
    units = {c["name"]: c["units"] for c in catalog["products"]["daily"]["columns"]}
    frame = read_day(a.data_dir / "meteorology-daily.parquet", a.date)
    partial = read_day(a.data_dir / "meteorology-daily.parquet", a.partial_date)
    if frame.empty or partial.empty:
        p.error("Both dates must exist in delivered data.")
    a.output_dir.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10})
    stats = {}
    for group, columns in GROUPS.items():
        entries = [(frame, field, title, a.date) for field, title in columns]
        if group == "atmosphere":
            entries.append(
                (
                    partial,
                    "PRECIPITATION_MM",
                    "Precipitation · partial day",
                    a.partial_date,
                )
            )
        fig, axes = plt.subplots(
            2,
            2 if group == "temperature" else 3,
            figsize=(11 if group == "temperature" else 15, 10),
        )
        for ax, (df, field, title, day) in zip(axes.flat, entries):
            values = df[field].to_numpy(dtype=float)
            polygons = []
            for cell in df.H3_INDEX:
                lat, lon = np.asarray(h3.cell_to_boundary(cell)).T
                polygons.append(np.column_stack(project(lat, lon)))
            finite = values[np.isfinite(values)]
            cmap = "cividis"
            low, high = (
                (float(finite.min()), float(finite.max())) if finite.size else (0, 1)
            )
            if group == "temperature":
                low, high = 0, 20
            if field.startswith(("U_", "V_")):
                low, high, cmap = -10, 10, "PuOr"
            if field.startswith("WIND_SPEED"):
                low, high = 0, 15
            if "PCT" in field:
                low, high = 0, 100
            if "PRESSURE" in field:
                low, high = 990, 1030
            if "DIRECTION" in field:
                low, high, cmap = 0, 360, "twilight"
            if field == "PRECIPITATION_MM":
                low, high = 0, 30
            norm = matplotlib.colors.Normalize(low, high)
            colors = plt.get_cmap(cmap)(norm(values))
            colors[~np.isfinite(values)] = matplotlib.colors.to_rgba("#d5d9dd")
            collection = PolyCollection(
                polygons, facecolors=colors, edgecolors="#ffffff", linewidths=0.13
            )
            ax.add_collection(collection)
            for poly, missing in zip(polygons, ~np.isfinite(values)):
                if missing:
                    ax.add_collection(
                        PolyCollection(
                            [poly],
                            facecolors="none",
                            edgecolors="#68717b",
                            linewidths=0.2,
                            hatch="///",
                        )
                    )
            ax.set_xlim(*project([49, 49], [-130, -121])[0])
            ax.set_ylim(*project([45.7, 51.7], [-126, -126])[1])
            ax.set_aspect("equal")
            ax.set_xticks(
                project([49, 49, 49], [-129, -126, -123])[0],
                ["129°W", "126°W", "123°W"],
            )
            ax.set_yticks(
                project([46, 48, 50], [-126, -126, -126])[1], ["46°N", "48°N", "50°N"]
            )
            ax.grid(alpha=0.15)
            ax.set_title(
                f"{title}\n{day} UTC · {np.isfinite(values).sum()}/{len(values)} supported",
                fontsize=11,
            )
            if finite.size:
                fig.colorbar(
                    plt.cm.ScalarMappable(norm=norm, cmap=cmap),
                    ax=ax,
                    shrink=0.75,
                    pad=0.03,
                    label=units[field],
                    extend="both",
                )
            else:
                ax.text(
                    0.5,
                    0.48,
                    "No complete daily support",
                    transform=ax.transAxes,
                    ha="center",
                    fontsize=10,
                    bbox={"facecolor": "white", "alpha": 0.9, "edgecolor": "none"},
                )
            stats[f"{day}/{field}"] = {
                "rows": len(values),
                "nulls": int(np.isnan(values).sum()),
                "min": float(finite.min()) if finite.size else None,
                "max": float(finite.max()) if finite.size else None,
            }
        for ax in list(axes.flat)[len(entries) :]:
            ax.set_visible(False)
        fig.suptitle(f"Coastal ERA5 · {group.title()}", fontsize=19, y=0.98)
        fig.legend(
            handles=[
                Patch(
                    facecolor="#d5d9dd",
                    edgecolor="#68717b",
                    hatch="///",
                    label="No data / incomplete support",
                )
            ],
            loc="lower center",
            bbox_to_anchor=(0.5, 0.06),
            frameon=False,
        )
        fig.text(
            0.5,
            0.025,
            "Source: delivered MarineCast ERA5 / Copernicus C3S–ECMWF (CC BY 4.0). H3 R5, nearest 0.25° source (~31 km).\nFull H3 polygons; blank space is outside reporting cells. Equirectangular projection, 49°N. No added H3-scale weather detail.",
            ha="center",
            fontsize=10,
        )
        fig.subplots_adjust(
            left=0.06, right=0.96, top=0.91, bottom=0.14, hspace=0.3, wspace=0.3
        )
        fig.savefig(a.output_dir / f"era5-{group}.png", dpi=130, facecolor="white")
        plt.close(fig)
    (a.output_dir / "map-statistics.json").write_text(
        json.dumps(stats, indent=2) + "\n"
    )


if __name__ == "__main__":
    main()
