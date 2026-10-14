"""Campaign regressions: wait without inference and review inconclusive reports."""

import argparse
import json

import pytest

from conjecture_solver.research_continuation import submit
from conjecture_solver.research_service import ResearchService, put
from conjecture_solver.research_supervisor import ResearchSupervisor


def fixture(tmp_path):
    service = ResearchService.create(
        tmp_path / "study",
        "An unchanged physical hypothesis",
        wall_seconds=600,
        report_reserve_seconds=60,
    )
    instructions = tmp_path / "instructions"
    instructions.write_text("Report uncertainty honestly and await useful feedback.")
    supervisor = ResearchSupervisor(
        argparse.Namespace(
            campaign=service.root,
            state_dir=service.root / "supervisor",
            instructions_file=instructions,
            backend="codex",
            model="fixture",
            judge_model="fixture",
            wall_seconds=600,
            turn_seconds=120,
        )
    )
    return service, supervisor


def verdict(directory, decision="accepted"):
    body = dict(
        decision=decision,
        summary="Bounded findings; the hypothesis remains unresolved.",
        issues=[] if decision == "accepted" else ["Include the failed control."],
    )
    (directory / "response.json").write_text(
        json.dumps(
            dict(type="item.completed", item=dict(type="agent_message", text=json.dumps(body)))
        )
        + "\n"
        + json.dumps(dict(type="turn.completed", usage={}))
        + "\n"
    )


def test_waiting_for_operator_spends_no_worker_calls_and_wakes_on_steering(tmp_path, monkeypatch):
    service, supervisor = fixture(tmp_path)
    deadline = service.manifest["deadline"]
    service.wait("Await revised direction from the operator", for_operator=True)
    launches, sleeps = [], []
    monkeypatch.setattr(supervisor, "prompt", lambda: "New operator direction")

    def sleep(seconds):
        assert not launches
        sleeps.append(seconds)
        assert len(sleeps) <= 9, "Steering must wake the parked worker"
        if len(sleeps) == 8:
            submit(service.root, "Resume with the saved controls and inspect their results.")

    def launch(*args, **kwargs):
        assert not kwargs.get("judge")
        launches.append(True)
        supervisor.cancelled = True
        return 0

    monkeypatch.setattr("conjecture_solver.research_supervisor.time.sleep", sleep)
    monkeypatch.setattr(supervisor, "launch", launch)
    assert supervisor.run() == 0
    assert launches == [True] and len(sleeps) == 8
    assert service.waiting_status()["status"] == "woken"
    assert service.manifest["deadline"] == deadline
    assert not service.status()["completed"]


def test_report_assessment_runs_before_reserve_and_never_closes_claim(tmp_path, monkeypatch):
    service, supervisor = fixture(tmp_path)
    (service.work / "calc.py").write_text("print('retained failed control')")
    monkeypatch.setattr(service, "_spawn_experiment", lambda record: None)
    failed = service.run("calc.py", outputs=["result.json"], stage="exploration")
    failed.update(status="failed", error="A retained numerical control failed")
    put(service.root / "experiments" / (failed["id"] + ".json"), failed)
    (service.work / "RESULTS.md").write_text(
        "The question remains unresolved. A numerical control failed; test the boundary work next."
    )
    before = (service.root / "research.json").read_bytes()
    request = service.assess_report()
    service.wait("Await the report reviewer", reports=[request["id"]])
    calls = []

    def launch(directory, prompt, judge=False):
        calls.append(judge)
        assert judge and "not a claim-approval" in prompt
        assert (
            supervisor.launch_deadline
            <= service.manifest["finalization_policy"]["compute_deadline"]
        )
        verdict(directory)
        return 0

    monkeypatch.setattr(supervisor, "launch", launch)
    supervisor.process_report_requests()
    assessed = service.report_assessment_status(request["id"])
    assert assessed["status"] == "resolved" and assessed["assessment"]["decision"] == "accepted"
    assert service.waiting_status()["status"] == "woken"
    assert not service.status()["completed"] and not service.status()["reviews"]
    assert (service.root / "research.json").read_bytes() == before
    assert service.assess_report()["id"] == request["id"]
    supervisor.process_report_requests()
    assert calls == [True]  # An unchanged report never creates another reviewer call.


def test_report_revision_is_feedback_and_changed_report_gets_new_identity(tmp_path, monkeypatch):
    service, supervisor = fixture(tmp_path)
    report = service.work / "RESULTS.md"
    report.write_text("Exploratory findings without the failed control.")
    request = service.assess_report()
    monkeypatch.setattr(supervisor, "launch", lambda d, p, **kw: verdict(d, "needs_revision") or 0)
    supervisor.process_report_requests()
    assert (
        service.report_assessment_status(request["id"])["assessment"]["decision"]
        == "needs_revision"
    )
    report.write_text(
        "Exploratory findings including the failed control and an unresolved conclusion."
    )
    assert service.assess_report()["id"] != request["id"]
    assert not service.status()["completed"]


def test_frozen_report_tamper_is_rejected_without_provider_call(tmp_path, monkeypatch):
    service, supervisor = fixture(tmp_path)
    (service.work / "RESULTS.md").write_text("A bounded report with uncertainty.")
    request = service.assess_report()
    (service.root / request["report"]).write_text("Replaced with a different conclusion.")
    monkeypatch.setattr(
        supervisor, "launch", lambda *a, **kw: pytest.fail("Do not review changed evidence")
    )
    supervisor.process_report_requests()
    failed = service._read("report_requests", request["id"])
    assert failed["status"] == "failed" and "changed" in failed["error"]
    assert not service.status()["completed"]


def test_settled_job_returns_immediate_feedback_instead_of_parking(tmp_path, monkeypatch):
    service, _ = fixture(tmp_path)
    (service.work / "calc.py").write_text("print('fixture')")
    monkeypatch.setattr(service, "_spawn_experiment", lambda r: None)
    job = service.run("calc.py", outputs=["result.json"], stage="exploration")
    job.update(status="failed", error="A retained numerical failure")
    put(service.root / "experiments" / (job["id"] + ".json"), job)
    wait = service.wait("Await the numerical result", experiments=[job["id"]])
    assert wait["status"] == "woken" and wait["wake_events"][0]["status"] == "failed"


def test_report_and_wait_requests_cannot_extend_an_expired_campaign(tmp_path, monkeypatch):
    service, _ = fixture(tmp_path)
    (service.work / "RESULTS.md").write_text("An unresolved result.")
    monkeypatch.setattr("time.time", lambda: service.manifest["deadline"] + 1)
    with pytest.raises(ValueError, match="deadline"):
        service.assess_report()
    with pytest.raises(ValueError, match="deadline"):
        service.wait("Await operator guidance", for_operator=True)


def test_steering_arriving_before_wait_is_not_lost(tmp_path):
    service, _ = fixture(tmp_path)
    submit(service.root, "Inspect this new evidence before waiting for my next message.")
    wait = service.wait("Await operator feedback", for_operator=True)
    assert wait["status"] == "woken"
    assert wait["wake_events"][0]["kind"] == "operator_steering"


def test_generated_client_exposes_report_and_wait_operations(tmp_path):
    service, _ = fixture(tmp_path)
    import importlib.util

    spec = importlib.util.spec_from_file_location("generated_lab", service.work / "lab.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    (service.work / "RESULTS.md").write_text("Unresolved; examine the remaining uncertainty.")
    request = module.lab.assess_report()
    assert (
        module.lab.wait("Await independent report feedback", reports=[request["id"]])["status"]
        == "waiting"
    )
    assert module.lab.report_assessment_status(request["id"])["status"] == "queued"
