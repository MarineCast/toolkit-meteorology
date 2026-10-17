from pathlib import Path
import hashlib
import json
import h3
import pytest
from meteorology.marine_reporting import load_reporting_membership
FIXTURE = Path(__file__).parent / 'fixtures/study.coastal-policy.v1.json'

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


def test_declared_mask_and_manifest_supply_exact_witness(tmp_path):
    original, study, mask, _ = registry(tmp_path)
    config = json.loads(study.read_text())
    declared = config["grid_registry"]
    declared.update(
        mask_relative_path="../Data/mask.fixture",
        mask_hash_policy="sha256_exact_file_bytes",
        manifest_relative_path="../Data/mask-manifest.json",
    )
    manifest = {
        field: declared[field]
        for field in ["mask_revision", "mask_sha256", "mask_hash_policy", "memberships"]
    }
    manifest_path = tmp_path / "Data/mask-manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    declared["manifest_sha256"] = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    study.write_text(json.dumps(config))
    member = load_reporting_membership(study)
    assert member.cells == original.cells
    manifest_path.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="manifest SHA256"):
        load_reporting_membership(study)
