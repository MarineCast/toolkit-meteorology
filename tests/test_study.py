from pathlib import Path
import hashlib
import json
import pytest
from meteorology.study import load_study_config, planning_report, validate_study_identity
FIXTURE = Path(__file__).parent / 'fixtures/study.coastal-policy.v1.json'
COASTAL_FIXTURE = FIXTURE
CONFIG_HASH = COASTAL_HASH = 'bacf22ea2b0beb32d1ef5607f52b2f6104419dd329edf25657bca196acc8018c'
GEOMETRY_HASH = '6d79e4dfd29a4ada66625e20fdcd01e7bfe6076bf6ebb4c449581eb3a0cdfb68'

def test_portable_explicit_path_hashes_and_planning_gate(tmp_path, monkeypatch):
    config_dir = tmp_path / "portable" / "config"
    config_dir.mkdir(parents=True)
    selected = config_dir / "study.json"
    selected.write_bytes(FIXTURE.read_bytes())
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARINECAST_STUDY_CONFIG", str(tmp_path / "wrong.json"))
    identity = load_study_config(selected, planning=True)
    assert identity["config_sha256"] == CONFIG_HASH
    assert identity["geometry_sha256"] == GEOMETRY_HASH
    assert Path(identity["resolved_data_root"]) == tmp_path / "portable" / "Data"
    assert identity["requested_time"]["end_exclusive"] == "2027-01-01"
    assert not (tmp_path / "portable" / "Data").exists()
    with pytest.raises(ValueError, match="proposed"):
        load_study_config(selected)
    with pytest.raises(FileNotFoundError):
        load_study_config(tmp_path / "missing.json", planning=True)
    monkeypatch.setenv("MARINECAST_STUDY_CONFIG", str(selected))
    assert planning_report()["study_identity"]["config_sha256"] == CONFIG_HASH


@pytest.mark.parametrize(
    "mutation,match",
    [
        (lambda c: c.update(schema_version=2), "constant"),
        (lambda c: c["domain"]["bbox_wgs84"].__setitem__(0, -130), "geometry_sha256"),
        (lambda c: c["time"].update(start="2027-01-01"), "interval"),
        (lambda c: c["storage"].update(data_root="/tmp/nonportable"), "relative"),
        (lambda c: c["products"]["meteorology"].update(h3_resolution=7), "constant"),
    ],
)
def test_invalid_selected_study_fails(tmp_path, mutation, match):
    config = json.loads(FIXTURE.read_text())
    mutation(config)
    p = tmp_path / "study.json"
    p.write_text(json.dumps(config))
    with pytest.raises(ValueError, match=match):
        load_study_config(p, planning=True)


def test_duplicate_nonfinite_and_missing_explicit_no_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("MARINECAST_STUDY_CONFIG", str(FIXTURE))
    p = tmp_path / "invalid.json"
    for content, match in [
        (' {"schema_version":1,"schema_version":1}', "duplicate"),
        (' {"schema_version":NaN}', "NaN"),
    ]:
        p.write_text(content)
        with pytest.raises(ValueError, match=match):
            load_study_config(p, planning=True)
    with pytest.raises(FileNotFoundError):
        load_study_config(tmp_path / "absent.json", planning=True)


def test_identity_uses_one_captured_config_snapshot(tmp_path, monkeypatch):
    import meteorology.study as study

    selected = tmp_path / "study.json"
    original = FIXTURE.read_bytes()
    selected.write_bytes(original)
    replacement = json.loads(original)
    replacement["time"]["start"] = "2010-01-01"
    real_parse = study.parse_json_bytes

    def replace_after_capture(raw):
        if raw == original:
            selected.write_text(json.dumps(replacement))
        return real_parse(raw)

    monkeypatch.setattr(study, "parse_json_bytes", replace_after_capture)
    identity = load_study_config(selected, planning=True)
    assert identity["requested_time"]["start"] == "2009-01-01"
    assert identity["config_sha256"] == CONFIG_HASH
    assert identity["raw_file_sha256"] == hashlib.sha256(original).hexdigest()
    assert hashlib.sha256(selected.read_bytes()).hexdigest() != identity["raw_file_sha256"]


@pytest.mark.parametrize(
    "timestamp", ["2026-10-06T00:00:00Z", "2026-10-06T01:00:00+01:00", "2026-10-06T00:00:00"]
)
def test_current_approval_fields_and_timezone(tmp_path, timestamp):
    config = json.loads(FIXTURE.read_bytes())
    config["domain"].update(
        status="approved",
        revision_note="Pending smaller rectangle",
        approval=dict(
            approved_at=timestamp,
            source_message_id="test",
            scope="rectangular_selection_only",
            statement="test approval",
        ),
    )
    selected = tmp_path / "study.json"
    selected.write_text(json.dumps(config))
    if timestamp.endswith("00:00:00"):
        with pytest.raises(ValueError, match="requires timezone"):
            load_study_config(selected, planning=True)
    else:
        identity = load_study_config(selected, planning=True)
        validate_study_identity(identity)


def test_approved_status_requires_provenance(tmp_path):
    config = json.loads(FIXTURE.read_bytes())
    config["domain"]["status"] = "approved"
    selected = tmp_path / "study.json"
    selected.write_text(json.dumps(config))
    with pytest.raises(ValueError, match="approval provenance"):
        load_study_config(selected, planning=True)


def test_approved_coastal_policy_is_planning_envelope_only():
    identity = load_study_config(COASTAL_FIXTURE, planning=True)
    assert identity["config_sha256"] == COASTAL_HASH
    domain = identity["study_config"]["domain"]
    assert domain["selection_policy"]["status"] == "approved"
    assert domain["selection_policy"]["offshore_distance_m"] == 22224
    assert domain["bbox_role"] == "acquisition_planning_envelope_only"
    assert domain["geometry_status"] == "pending_qualified_coastline_validation"
    validate_study_identity(identity)
    with pytest.raises(ValueError, match="proposed"):
        load_study_config(COASTAL_FIXTURE)


@pytest.mark.parametrize("pending", ["geometry", "mask", "registry", "policy"])
def test_policy_approval_cannot_bypass_pending_support(tmp_path, pending):
    config = json.loads(COASTAL_FIXTURE.read_bytes())
    config["domain"]["status"] = "approved"
    config["domain"]["approval"] = dict(
        approved_at="2026-10-06T00:00:00Z",
        source_message_id="test",
        scope="rectangular_selection_only",
        statement="test approval",
    )
    config["domain"]["geometry_status"] = "source_relative_validated"
    policy = config["domain"]["selection_policy"]
    policy["mask_status"] = "source_relative_validated"
    config["grid_registry"].update(
        status="validated",
        mask_revision="synthetic-test-only",
        mask_sha256="a" * 64,
        memberships=[
            dict(
                resolution=5,
                role="water_reporting",
                relative_path="test.json",
                count=1,
                sha256="b" * 64,
            )
        ],
    )
    if pending == "geometry":
        config["domain"]["geometry_status"] = "pending_qualified_coastline_validation"
    elif pending == "mask":
        policy["mask_status"] = "pending_source_qualified_build"
    elif pending == "registry":
        config["grid_registry"]["status"] = "pending_validated_marine_mask"
    else:
        policy["status"] = "proposed"
    selected = tmp_path / "study.json"
    selected.write_text(json.dumps(config))
    load_study_config(selected, planning=True)
    with pytest.raises(ValueError, match="validated coastal mask, geometry and registry"):
        load_study_config(selected)


@pytest.mark.parametrize(
    "mutation,match",
    [
        (lambda c: c["domain"].pop("bbox_role"), "missing="),
        (lambda c: c["domain"].pop("geometry_status"), "missing="),
        (lambda c: c["domain"]["selection_policy"].update(approval=None), "approval provenance"),
        (
            lambda c: c["domain"]["selection_policy"]["approval"].update(
                approved_at="2026-10-06T00:00:00"
            ),
            "requires timezone",
        ),
        (lambda c: c["domain"]["selection_policy"].update(offshore_distance_m=24000), "constant"),
    ],
)
def test_policy_contract_rejects_invalid_selection(tmp_path, mutation, match):
    config = json.loads(COASTAL_FIXTURE.read_bytes())
    mutation(config)
    selected = tmp_path / "study.json"
    selected.write_text(json.dumps(config))
    with pytest.raises(ValueError, match=match):
        load_study_config(selected, planning=True)


def test_omitted_selection_policy_cannot_bypass_pending_geometry(tmp_path):
    config = json.loads(COASTAL_FIXTURE.read_bytes())
    config["domain"]["status"] = "approved"
    config["domain"]["approval"] = dict(
        approved_at="2026-10-06T00:00:00Z",
        source_message_id="test",
        scope="rectangular_selection_only",
        statement="test approval",
    )
    config["domain"].pop("selection_policy")
    config["grid_registry"].update(
        status="validated",
        mask_revision="synthetic",
        mask_sha256="a" * 64,
        memberships=[
            dict(
                resolution=5, role="water_reporting", relative_path="test", count=1, sha256="b" * 64
            )
        ],
    )
    selected = tmp_path / "omitted-policy.json"
    selected.write_text(json.dumps(config))
    for planning in (True, False):
        with pytest.raises(ValueError, match="selection_policy"):
            load_study_config(selected, planning=planning)
    assert not (tmp_path / "Data").exists()


def test_legacy_rectangle_config_requires_explicit_coastal_contract_migration():
    with pytest.raises(ValueError, match="selection_policy"):
        load_study_config(
            Path(__file__).parent / "fixtures" / "study.proposed.v1.json", planning=True
        )
