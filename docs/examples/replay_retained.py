import argparse
import json
import pathlib
import subprocess
import time
import shutil
import platform
import pandas as pd
import pyarrow.parquet as pq
import meteorology
from meteorology.regional import source_root

p = argparse.ArgumentParser(
    description="Offline two-day retained-source replay; use an installed wheel outside the checkout."
)
p.add_argument("--workspace", type=pathlib.Path, required=True)
p.add_argument("--work", type=pathlib.Path, required=True)
a = p.parse_args()
root = a.workspace.resolve()
work = a.work.resolve()
if work.exists():
    p.error("Choose a new scratch work directory.")
if root in work.parents:
    p.error("Keep scratch work outside the workspace.")
work.mkdir(parents=True)
output = work / "replay"
cache = source_root(output, work / "cache")
cache.parent.mkdir(exist_ok=True)
source = root / ".marinecast-work/meteorology/frontier-20261026"
if platform.system() == "Darwin":
    subprocess.run(["cp", "-cR", str(source), str(cache)], check=True)
else:
    shutil.copytree(source, cache)
(cache / "owner.json").write_text(
    json.dumps(dict(owner="meteorology-transient-sources-v1", destination=str(output)))
)
start = time.monotonic()
r = meteorology.get_weather(
    "2026-10-19",
    "2026-10-20",
    meteorology.ALLOWED_VARIABLES,
    study_config=root / "config/study.v1.json",
    output_dir=output,
    cache_dir=work / "cache",
    transfer_bytes=1,
    max_seconds=600,
    allow_incomplete_final_day=True,
    verbosity=False,
    rebuild=False,
)
meteorology.check_weather(r.catalog_path, study_config=root / "config/study.v1.json")
c = json.loads(r.catalog_path.read_text())
e = {
    "review_date": "2026-10-29",
    "installed_module": str(meteorology.__file__),
    "elapsed_seconds": time.monotonic() - start,
    "acquisition": c["acquisition"],
    "days": ["2026-10-19", "2026-10-20"],
    "comparison": {},
    "limitations": [
        "Two-day recent-vintage replay, not full historical reconstruction",
        "No live acquisition; transfer cap 1 byte forces missing source requests to fail",
    ],
}
for frequency, key in [("daily", "DATE"), ("hourly", "DATETIME")]:
    p = root / "Data/Meteorology" / f"meteorology-{frequency}.parquet"
    pf = pq.ParquetFile(p)
    # Delivered row groups are one UTC day each; use metadata for bounded reads.
    tables = []
    for i in range(pf.num_row_groups):
        st = pf.metadata.row_group(i).column(1).statistics
        if str(st.max)[:10] >= "2026-10-19" and str(st.min)[:10] <= "2026-10-20":
            tables.append(pf.read_row_group(i).to_pandas())
    delivered = pd.concat(tables).sort_values(["H3_INDEX", key]).reset_index(drop=True)
    replay = (
        pd.read_parquet(output / f"meteorology-{frequency}.parquet")
        .sort_values(["H3_INDEX", key])
        .reset_index(drop=True)
    )
    pd.testing.assert_frame_equal(delivered, replay, check_exact=True)
    e["comparison"][frequency] = {
        "rows": len(replay),
        "cells": replay.H3_INDEX.nunique(),
        "exact_key_value_null_parity": True,
        "nulls": replay.isna().sum().to_dict(),
        "statistics": replay.select_dtypes("number").agg(["min", "max", "mean"]).to_dict(),
    }
(work / "replay-evidence.json").write_text(json.dumps(e, indent=2, allow_nan=False) + "\n")
print(json.dumps({k: v for k, v in e.items() if k != "comparison"}, indent=2))
