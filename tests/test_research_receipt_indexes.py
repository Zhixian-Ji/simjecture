"""Large transport catalogues must not prevent a scientific methods review."""

import hashlib
import json

import pytest

from conjecture_solver.research_receipts import inventory_reference, receipt_projection
from conjecture_solver.research_service import ResearchService, put, sha
from conjecture_solver.review_evidence import EvidenceRequest, fetch_evidence


def fixture(tmp_path, monkeypatch):
    service = ResearchService.create(tmp_path / "study", "A finite hypothesis")
    (service.work / "calc.py").write_text("print('retained fixture')")
    monkeypatch.setattr(service, "_spawn_experiment", lambda record: None)
    record = service.run("calc.py", outputs=["result.json"], stage="exploration")
    workspace = service.root / "experiments" / record["id"] / "workspace"
    (workspace / "result.json").write_text('{"energy_error": 0.001}')
    record.update(
        status="succeeded",
        execution=dict(returncode=0, wall_seconds=1),
        artifacts={"result.json": dict(sha256=sha(workspace / "result.json"), bytes=23)},
        remote_artifacts={
            f"raw/fields{i:05d}": dict(bytes=1024, sha256=f"{i:064x}") for i in range(32854)
        },
    )
    path = service.root / "experiments" / (record["id"] + ".json")
    put(path, record)
    return service, record, path


def test_large_inventory_fits_method_packet_without_changing_receipt(tmp_path, monkeypatch):
    service, record, path = fixture(tmp_path, monkeypatch)
    original = path.read_bytes()
    method = service.method(
        source="calc.py",
        model="A declared numerical fixture",
        geometry="A fixed finite domain",
        observable="Integrated energy error",
        validation="Use the recorded energy control",
        rationale="Qualify this bounded diagnostic before collecting evidence",
        scope="instrument",
        validation_experiments=[record["id"]],
    )
    assert len(json.dumps(method).encode()) < 700_000
    projected = method["evidence"][0]["record"]
    assert "remote_artifacts" not in projected
    assert projected["remote_artifact_index"]["count"] == 32854
    assert projected["receipt_reference"]["sha256"] == hashlib.sha256(original).hexdigest()
    assert path.read_bytes() == original
    assert projected["stage"] == "exploration" and projected["status"] == "succeeded"


def test_selected_inventory_entries_are_verified_and_bounded(tmp_path, monkeypatch):
    service, record, _ = fixture(tmp_path, monkeypatch)
    reference = inventory_reference(service, record)
    request = EvidenceRequest(
        experiment=record["id"],
        inventory=dict(sha256=reference["sha256"], names=["raw/fields00001", "raw/fields00007"]),
    ).model_dump()
    result = fetch_evidence(service, [request])[0]
    assert set(result["inventory"]["entries"]) == {"raw/fields00001", "raw/fields00007"}
    assert len(json.dumps(result).encode()) < 12000
    assert result["stage"] == "exploration"
    (service.root / reference["path"]).write_text("{}")
    assert "changed" in fetch_evidence(service, [request])[0]["error"]


def test_wrong_inventory_hash_or_missing_entry_cannot_be_substituted(tmp_path, monkeypatch):
    service, record, _ = fixture(tmp_path, monkeypatch)
    reference = inventory_reference(service, record)
    for inventory in [dict(sha256="0" * 64), dict(sha256=reference["sha256"], names=["absent"])]:
        request = EvidenceRequest(experiment=record["id"], inventory=inventory).model_dump()
        assert "error" in fetch_evidence(service, [request])[0]


def test_inventory_pagination_preserves_identity(tmp_path, monkeypatch):
    service, record, _ = fixture(tmp_path, monkeypatch)
    reference = inventory_reference(service, record)
    request = EvidenceRequest(
        experiment=record["id"], inventory=dict(sha256=reference["sha256"], offset=10, limit=3)
    ).model_dump()
    result = fetch_evidence(service, [request])[0]["inventory"]
    assert len(result["entries"]) == 3 and result["next_offset"] == 13
    assert result["index"] == reference


def test_failure_and_large_console_output_remain_traceable(tmp_path, monkeypatch):
    service, record, path = fixture(tmp_path, monkeypatch)
    record.update(status="failed", error="The numerical control failed")
    record["execution"]["stderr"] = "diagnostic\n" * 10000
    put(path, record)
    projected = receipt_projection(service, record)
    assert projected["status"] == "failed" and projected["error"] == record["error"]
    assert projected["execution"]["stderr_excerpt"]
    assert projected["receipt_reference"]["sha256"] == sha(path)
    assert len(projected["execution"]["stderr"]) <= 8000


def test_inventory_and_output_requests_are_distinct():
    with pytest.raises(ValueError, match="not both"):
        EvidenceRequest(
            experiment="exp_fixture", output="result.json", inventory=dict(sha256="0" * 64)
        )
