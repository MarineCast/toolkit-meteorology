from dataclasses import replace
from pathlib import Path
import hashlib
import json

import h3
import pandas as pd
import pytest
from shapely.geometry import box

from meteorology.marine_reporting import (
    load_reporting_membership,
    nearest_native_crosswalk,
    project_native_daily,
)
from meteorology.study import load_study_config

FIXTURE = Path(__file__).parent / "fixtures/study.coastal-policy.v1.json"


def registry(tmp_path):
    center = h3.latlng_to_cell(49, -125, 5)
    cells = sorted(set(h3.grid_disk(center, 1)) | {h3.latlng_to_cell(51, -128, 5)})
    raw = ("\n".join(cells) + "\n").encode()
    config = json.loads(FIXTURE.read_bytes())
    config["domain"]["status"] = "approved"
    config["domain"]["approval"] = dict(
        approved_at="2026-10-06T00:00:00Z",
        source_message_id="synthetic",
        scope="rectangular_selection_only",
        statement="SYNTHETIC TEST ONLY",
    )
    config["domain"]["geometry_status"] = "source_relative_validated"
    config["domain"]["selection_policy"]["mask_status"] = "source_relative_validated"
    mask_bytes = b"SYNTHETIC TEST MASK, NOT REAL MARINE GEOMETRY"
    config["grid_registry"].update(
        status="validated",
        mask_revision="synthetic-test-only",
        mask_sha256=hashlib.sha256(mask_bytes).hexdigest(),
        memberships=[
            dict(
                resolution=5,
                role="water_reporting",
                relative_path="../Data/synthetic.ids",
                count=len(cells),
                sha256=hashlib.sha256(raw).hexdigest(),
            )
        ],
    )
    data = tmp_path / "Data"
    data.mkdir()
    (data / "synthetic.ids").write_bytes(raw)
    mask = data / "mask.fixture"
    mask.write_bytes(mask_bytes)
    directory = tmp_path / "config"
    directory.mkdir()
    study = directory / "study.json"
    study.write_text(json.dumps(config))

    # Exact frozen owner interface; geometry qualification remains synthetic here.
    def reader(raw):
        return raw.decode().splitlines()

    member = load_reporting_membership(
        study, mask_path=mask, mask_hash_policy="sha256_exact_file_bytes"
    )
    return member, study, mask, reader


def native(source="ERA5", day="2024-01-01"):
    rows = []
    for lat in [48.75, 49, 49.25, 49.5]:
        for lon in [-125.5, -125.25, -125, -124.75]:
            index = len(rows)
            prefix = source + "_TEMPERATURE_2M_C"
            rows.append(
                dict(
                    DATE=day,
                    TIMEZONE="UTC",
                    SOURCE_MODEL=source,
                    SOURCE_GRID_HASH="a" * 64,
                    SOURCE_GRID_INDEX=index,
                    SOURCE_LAT=lat,
                    SOURCE_LON=lon,
                    **{
                        prefix: float(index),
                        prefix + "_STATUS": "COMPLETE",
                        prefix + "_VALID_HOURS": 24,
                        prefix + "_EXPECTED_HOURS": 24,
                        prefix + "_COVERAGE_FRACTION": 1.0,
                    },
                )
            )
    return pd.DataFrame(rows)


def test_pending_real_geometry_fails_before_reader_or_artifact_io(tmp_path):
    def forbidden(_):
        pytest.fail("reader invoked for pending production selection")

    with pytest.raises(ValueError, match="proposed"):
        load_reporting_membership(
            FIXTURE, mask_path=tmp_path / "missing", mask_hash_policy="sha256_exact_file_bytes"
        )
    assert list(tmp_path.iterdir()) == []


def test_exact_membership_hash_count_and_snapshot(tmp_path, monkeypatch):
    member, study, mask, reader = registry(tmp_path)
    original = (tmp_path / "Data/synthetic.ids").read_bytes()
    assert member.canonical_membership_sha256 == hashlib.sha256(original).hexdigest()

    def replace_on_parse(raw):
        (tmp_path / "Data/synthetic.ids").write_bytes(b"changed after capture")
        return original_parse(raw)

    import meteorology.marine_reporting as marine

    original_parse = marine._parse_membership_ids
    monkeypatch.setattr(marine, "_parse_membership_ids", replace_on_parse)
    captured = load_reporting_membership(
        study, mask_path=mask, mask_hash_policy="sha256_exact_file_bytes"
    )
    assert captured.cells == member.cells
    assert captured.artifact_sha256 == hashlib.sha256(original).hexdigest()
    with pytest.raises(ValueError, match="artifact SHA256"):
        load_reporting_membership(study, mask_path=mask, mask_hash_policy="sha256_exact_file_bytes")


@pytest.mark.parametrize(
    "change,match",
    [
        ("mask", "mask SHA256"),
        ("count", "count"),
        ("unsorted", "sorted and unique"),
        ("uppercase", "lowercase"),
        ("path", "beneath shared Data"),
        ("role", "exactly one"),
    ],
)
def test_bad_registry_fails_closed(tmp_path, change, match):
    member, study, mask, reader = registry(tmp_path)
    config = json.loads(study.read_text())
    if change == "mask":
        mask.write_bytes(b"wrong")
    elif change == "count":
        config["grid_registry"]["memberships"][0]["count"] += 1
    elif change == "path":
        config["grid_registry"]["memberships"][0]["relative_path"] = "../outside"
    elif change == "role":
        config["grid_registry"]["memberships"][0]["role"] = "rectangle"
    else:
        raw = (
            "\n".join(
                reversed(member.cells)
                if change == "unsorted"
                else [c.upper() for c in member.cells]
            )
            + "\n"
        ).encode()
        (tmp_path / "Data/synthetic.ids").write_bytes(raw)
        config["grid_registry"]["memberships"][0]["sha256"] = hashlib.sha256(raw).hexdigest()
    study.write_text(json.dumps(config))
    with pytest.raises(ValueError, match=match):
        load_reporting_membership(study, mask_path=mask, mask_hash_policy="sha256_exact_file_bytes")


def test_era5_mapping_preserves_native_coarseness_and_unsupported_rows(tmp_path):
    member, _, _, _ = registry(tmp_path)
    source = native()
    unchanged = source.copy(deep=True)
    crosswalk, meta = nearest_native_crosswalk(source, member, source_family="ERA5")
    assert tuple(crosswalk.H3_INDEX) == member.cells
    assert meta["supported_cells"] == len(member.cells) - 1
    assert crosswalk.SOURCE_GRID_INDEX.dropna().duplicated().any()  # No finer weather information.
    output, receipt = project_native_daily(source, crosswalk, meta, member, source_family="ERA5")
    pd.testing.assert_frame_equal(source, unchanged)
    assert len(output) == len(member.cells)
    unsupported = output[output.SPATIAL_SUPPORT_STATUS != "SUPPORTED"].iloc[0]
    assert pd.isna(unsupported.ERA5_TEMPERATURE_2M_C)
    assert unsupported.ERA5_TEMPERATURE_2M_C_STATUS == "UNAVAILABLE"
    assert unsupported.ERA5_TEMPERATURE_2M_C_VALID_HOURS == 0
    assert unsupported.ERA5_TEMPERATURE_2M_C_COVERAGE_FRACTION == 0
    for row in output[output.SPATIAL_SUPPORT_STATUS == "SUPPORTED"].itertuples():
        assert (
            row.ERA5_TEMPERATURE_2M_C
            == source.set_index("SOURCE_GRID_INDEX").loc[
                row.SOURCE_GRID_INDEX, "ERA5_TEMPERATURE_2M_C"
            ]
        )
    assert receipt["requested_time"]["start"] == "2009-01-01"
    assert receipt["actual_daily_coverage"]["available_days"] == ["2024-01-01"]
    assert receipt["actual_daily_coverage"]["period_complete"] is False


def test_hrrr_footprint_and_distance_separate_from_era5(tmp_path):
    member, _, _, _ = registry(tmp_path)
    source = native("HRRR")
    with pytest.raises(ValueError, match="distance allowance"):
        nearest_native_crosswalk(source, member, source_family="HRRR")
    with pytest.raises(ValueError, match="native footprint"):
        nearest_native_crosswalk(source, member, source_family="HRRR", hrrr_max_distance_m=10000)
    crosswalk, meta = nearest_native_crosswalk(
        source,
        member,
        source_family="HRRR",
        hrrr_qualified_footprint=box(-125.5, 48.75, -124.75, 49.5),
        hrrr_max_distance_m=1,
    )
    assert meta["supported_cells"] == 0
    output, _ = project_native_daily(source, crosswalk, meta, member, source_family="HRRR")
    assert output.HRRR_TEMPERATURE_2M_C.isna().all()
    assert not any(c.startswith("ERA5_") for c in output.columns)


def test_crosswalk_tampering_family_mix_and_pending_membership_rejected(tmp_path):
    member, _, _, _ = registry(tmp_path)
    source = native()
    crosswalk, meta = nearest_native_crosswalk(source, member, source_family="ERA5")
    bad = crosswalk.copy()
    bad.loc[0, "SOURCE_GRID_INDEX"] = 0
    with pytest.raises(ValueError, match="checksum identity"):
        project_native_daily(source, bad, meta, member, source_family="ERA5")
    with pytest.raises(ValueError, match="separate"):
        nearest_native_crosswalk(source, member, source_family="HRRR")
    pending = replace(member, study_identity=load_study_config(FIXTURE, planning=True))
    with pytest.raises(ValueError, match="qualified geometry"):
        nearest_native_crosswalk(source, pending, source_family="ERA5")


@pytest.mark.parametrize(
    "kind", ["duplicate", "timezone", "coverage", "partial_value", "date", "grid"]
)
def test_bad_native_daily_support_fails(tmp_path, kind):
    member, _, _, _ = registry(tmp_path)
    source = native()
    crosswalk, meta = nearest_native_crosswalk(source, member, source_family="ERA5")
    if kind == "duplicate":
        source = pd.concat([source, source.iloc[[0]]], ignore_index=True)
    elif kind == "timezone":
        source.TIMEZONE = "America/Los_Angeles"
    elif kind == "coverage":
        source.loc[0, "ERA5_TEMPERATURE_2M_C_COVERAGE_FRACTION"] = 0.5
    elif kind == "partial_value":
        source.loc[
            0,
            [
                "ERA5_TEMPERATURE_2M_C_VALID_HOURS",
                "ERA5_TEMPERATURE_2M_C_COVERAGE_FRACTION",
                "ERA5_TEMPERATURE_2M_C_STATUS",
            ],
        ] = [23, 23 / 24, "PARTIAL"]
    elif kind == "date":
        source.DATE = "2008-12-31"
    else:
        source.loc[0, "SOURCE_LAT"] += 0.01
    with pytest.raises(ValueError):
        project_native_daily(source, crosswalk, meta, member, source_family="ERA5")


@pytest.mark.parametrize("bad", [b"abc\r\n", b"abc", b"\n", b" abc\n", b"abc\n\n", b"\xff\n"])
def test_exact_ascii_lf_interface_rejects_malformed_bytes(bad):
    from meteorology.marine_reporting import _parse_membership_ids

    with pytest.raises(ValueError):
        _parse_membership_ids(bad)


def test_interface_path_is_config_relative_and_format_pin_matches_owner(tmp_path):
    member, study, mask, _ = registry(tmp_path)
    assert (study.parent / "../Data/synthetic.ids").resolve() == tmp_path / "Data/synthetic.ids"
    packaged = (
        Path(__import__("meteorology").__file__).parent
        / "resources/registry-artifact-interface.v1.json"
    )
    interface = json.loads(packaged.read_text())
    assert interface["membership_file"]["line_ending"] == "LF"
    assert "directory containing selected study config" in interface["path_base"]
    with pytest.raises(ValueError, match="matching owner verifier"):
        load_reporting_membership(
            study, mask_path=mask, mask_hash_policy="canonical_geometry_unknown"
        )


def test_empty_owner_membership_is_zero_bytes_and_not_filled_from_bbox(tmp_path):
    member, study, mask, _ = registry(tmp_path)
    config = json.loads(study.read_text())
    config["grid_registry"]["memberships"][0].update(
        count=0, sha256=hashlib.sha256(b"").hexdigest()
    )
    study.write_text(json.dumps(config))
    (tmp_path / "Data/synthetic.ids").write_bytes(b"")
    member = load_reporting_membership(
        study, mask_path=mask, mask_hash_policy="sha256_exact_file_bytes"
    )
    assert member.cells == ()
    assert member.canonical_membership_sha256 == hashlib.sha256(b"").hexdigest()
    crosswalk, meta = nearest_native_crosswalk(native(), member, source_family="ERA5")
    result, _ = project_native_daily(native(), crosswalk, meta, member, source_family="ERA5")
    assert len(result) == 0


def test_foreign_metric_columns_do_not_blend_sources(tmp_path):
    member, _, _, _ = registry(tmp_path)
    source = native()
    crosswalk, meta = nearest_native_crosswalk(source, member, source_family="ERA5")
    source["HRRR_TEMPERATURE_2M_C"] = 0
    with pytest.raises(ValueError, match="cannot be blended"):
        project_native_daily(source, crosswalk, meta, member, source_family="ERA5")


def test_extra_source_metric_without_status_rejected(tmp_path):
    member, *_ = registry(tmp_path)
    frame = native()
    frame["ERA5_WIND_SPEED_10M_M_S"] = float("inf")
    cross, meta = nearest_native_crosswalk(frame, member, source_family="ERA5")
    with pytest.raises(ValueError, match="Uncontracted"):
        project_native_daily(frame, cross, meta, member, source_family="ERA5")


def test_canonical_cells_must_match_artifact_hash(tmp_path):
    member, *_ = registry(tmp_path)
    cells = tuple(sorted((*member.cells[:-1], h3.latlng_to_cell(51.5, -128, 5))))
    forged = replace(
        member,
        cells=cells,
        canonical_membership_sha256=hashlib.sha256(("\n".join(cells) + "\n").encode()).hexdigest(),
    )
    with pytest.raises(ValueError, match="pinned registry"):
        nearest_native_crosswalk(native(), forged, source_family="ERA5")


def test_exact_file_mask_rejects_directory(tmp_path):
    from meteorology.artifacts import checksum_path

    _, study, *_ = registry(tmp_path)
    directory = tmp_path / "mask-tree"
    directory.mkdir()
    (directory / "one").write_bytes(b"synthetic")
    config = json.loads(study.read_text())
    config["grid_registry"]["mask_sha256"] = checksum_path(directory)
    study.write_text(json.dumps(config))
    with pytest.raises(ValueError, match="regular file"):
        load_reporting_membership(
            study, mask_path=directory, mask_hash_policy="sha256_exact_file_bytes"
        )
