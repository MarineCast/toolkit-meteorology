# Shared marine H3 consumer integration

`meteorology.marine_reporting` consumes the domain owner's frozen
`registry-artifact-interface.v1.json`, packaged unchanged under resources. Shared
configuration/schema are unchanged. A real source-qualified coastal mask and R5
water_reporting registry are still required; the approved coastal policy alone
cannot enter these APIs. Current real production fails before artifact reads.

Membership bytes are ASCII-sorted, unique lowercase canonical H3 IDs, one per LF
line, no header/whitespace/blank lines, final newline for nonempty files. Empty
membership is zero bytes and stays empty, never filled from bbox. Exact byte SHA256,
count, resolution and water_reporting role must match the selected configuration.
Paths resolve relative to the selected config directory, then must stay beneath
resolved Data; the coordinated `../Data/...` path is supported. CWD/toolkit lookup,
rectangle fallback and source-role substitution are forbidden. The loader parses
and hashes a single captured snapshot. It records the canonical ID hash separately.

A materialized-mask witness is explicitly supplied. Its hash policy must be passed
from the owner's mask manifest, never guessed. The current witness verifier supports
`sha256_exact_file_bytes` with streaming checksums; another geometric/hash policy
requires the corresponding owner verifier before real integration. It does not
certify a synthetic geometry or coastline based only on hash equality. The final
mask hash policy/manifest and actual artifacts remain an integration dependency.

Native atmospheric source support is separate from positive-area water membership.
No cell is selected by centroid containment in water. Mapping samples the full H3
cell centroid on the source grid; it is not a clipped-water spatial mean. Full cell
polygons/clipped support remain retained in the owner-qualified mask/registry and
are referenced by pinned hashes, not rewritten by this meteorology consumer.

ERA5 mapping requires a complete 2D regular 0.25-degree distribution grid and keeps
its source indices/coordinates/distances. Several R5 cells may share one source
point; repeating those values adds no weather information. Grid-centre bounds are
used conservatively, with no outside-edge extrapolation. HRRR requires its separate
producer-qualified native footprint and positive spacing/distance allowance. It
uses its own native grid and metrics; there is no ERA5 fill for missing HRRR support.

Unsupported targets remain in the exact reporting universe with null metric values,
UNAVAILABLE status, valid count 0, expected count 24 and coverage 0. Supported rows
inherit native per-metric completeness and missingness unchanged. Crosswalk cache
reuse binds the source family/grid/coordinates, actual mapping bytes, config,
mask revision/hash and exact membership; malformed counts/statuses, source mixing,
duplicate point/day rows, UTC mismatch and dates outside the request fail. Actual
available native days are listed separately from requested 2009–2026. No intervening
or future day is manufactured or labeled period-complete.

These are in-memory consumer/projection interfaces verified with synthetic geometry,
member files and native source tables. They do not acquire weather, qualify the real
coastal mask, infer provider completeness or publish final Data artifacts. Source-
processing scratch checkpoints and independent synthetic QA are distinct from final
marine release eligibility. The real registry remains pending US water closure.

```sh
python -m pytest -q tests/test_marine_reporting.py tests/test_era5_resources.py tests/test_era5.py
python -m pytest -q
```
