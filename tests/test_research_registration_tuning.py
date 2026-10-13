"""Slow SSH probes recover without changing frozen identities or wall budgets."""

import json
import sys
from types import SimpleNamespace

import pytest

from conjecture_solver.execution_pool import Transport, WorkerTimeout, WorkerUnavailable
from conjecture_solver.research_service import Lab, ResearchService, put
from tests.test_research_director import supervisor
from tests.test_research_remote_registration import instrument
from tests.test_research_remote_registration import remote_study as _remote_study

remote_study = _remote_study


def tuning(timeout=120, attempts=2):
    return dict(timeout_seconds=timeout, max_attempts=attempts,
                reason="The full instrument inventory probe needs a longer response deadline.")


def test_real_transport_distinguishes_timeout_from_worker_rejection(tmp_path):
    transport = Transport(dict(
        id="native", kind="local", root=str(tmp_path), python=sys.executable
    ))
    argv = [sys.executable, "-c",
            "import time,json;time.sleep(.1);print(json.dumps(dict(ok=True,result={})))"]
    with pytest.raises(WorkerTimeout, match="timed out after 0.01 seconds"):
        transport.run(argv, {}, timeout=.01)
    assert transport.run(argv, {}, timeout=2) == {}
    rejected = [sys.executable, "-c", "print('{\"ok\":false,\"error\":\"invalid config\"}')"]
    with pytest.raises(ValueError, match="invalid config"):
        transport.run(rejected, {}, timeout=2)
    with pytest.raises(WorkerUnavailable, match="transport could not start"):
        transport.run([str(tmp_path / "absent")], {}, timeout=2)


def test_slow_registration_recovers_after_director_tuning(remote_study, tmp_path, monkeypatch):
    service, capabilities = remote_study
    instrument(capabilities, tmp_path, "slow-instrument")
    manifest = (service.root / "research.json").read_bytes()
    original = Transport.call
    timeouts = []

    def slow(self, method, **kwargs):
        if method == "probe":
            timeouts.append(kwargs["timeout"])
            if kwargs["timeout"] < 69:
                raise WorkerTimeout("Inventory takes 69 seconds; deadline was 60 seconds")
        return original(self, method, **kwargs)

    monkeypatch.setattr(Transport, "call", slow)
    with pytest.raises(WorkerUnavailable, match="ask the director"):
        service.register_capability("slow-instrument", machine="native")
    attempt = service.registration_status()["recent_attempts"][0]
    assert attempt["status"] == "unavailable"
    assert attempt["error_type"] == "WorkerTimeout"
    assert not service._all("capability_additions")
    service.tune_registration(tuning(attempts=1), director_decision="director_slow")
    assert service.register_capability("slow-instrument", machine="native")["name"] == (
        "slow-instrument--at-native"
    )
    assert timeouts == [60, 120]
    assert (service.root / "research.json").read_bytes() == manifest
    service.tune_registration(tuning(attempts=3), director_decision="director_retry")
    executable = tmp_path / "original" / "python-tool"
    executable.chmod(0o755)
    executable.write_text(executable.read_text() + "\n# changed original identity\n")
    with pytest.raises(ValueError, match="Existing capability identity is immutable"):
        service.register_capability("slow-instrument", machine="native")
    assert len(timeouts) == 3  # Semantic rejection was never retried.
    assert service.registration_status()["recent_attempts"][0]["status"] == "rejected"


def test_transient_failures_retry_but_worker_rejections_do_not(tmp_path, monkeypatch):
    service = ResearchService.create(tmp_path / "study", "Scientific target")
    service.tune_registration(tuning(attempts=3), director_decision="director_retry")
    monkeypatch.setattr("conjecture_solver.research_registration.time.sleep", lambda _: None)
    calls = []

    def call(method, **kwargs):
        calls.append(kwargs["timeout"])
        if len(calls) < 3:
            raise WorkerUnavailable("temporary connection loss")
        return {"ready": True}

    assert service.probe_registration(SimpleNamespace(call=call), name="candidate", machine="gpu")
    assert len(calls) == 3
    assert len(service._all("registration_attempts")) == 3

    def rejected(method, **kwargs):
        calls.append(kwargs["timeout"])
        raise ValueError("immutable worker configuration")

    with pytest.raises(ValueError, match="immutable worker"):
        service.probe_registration(SimpleNamespace(call=rejected), name="bad", machine="gpu")
    assert len(calls) == 4
    assert service.registration_status()["recent_attempts"][0]["status"] == "rejected"


def test_probes_and_retries_respect_compute_cutoff(tmp_path, monkeypatch):
    service = ResearchService.create(tmp_path / "study", "Scientific target",
                                     wall_seconds=1000, report_reserve_seconds=100)
    service.tune_registration(tuning(attempts=10), director_decision="director_bounded")
    now = [service.registration_deadline() - .25]
    monkeypatch.setattr("conjecture_solver.research_registration.time.time", lambda: now[0])
    monkeypatch.setattr("conjecture_solver.research_registration.time.sleep", lambda _: None)
    timeouts = []

    def call(method, **kwargs):
        timeouts.append(kwargs["timeout"])
        now[0] += 1
        raise WorkerTimeout("probe expired")

    with pytest.raises(ValueError, match="protected report reserve"):
        service.probe_registration(SimpleNamespace(call=call), name="candidate", machine="gpu")
    assert timeouts == [.25]
    assert now[0] < service.manifest["deadline"]
    with pytest.raises(ValueError, match="compute budget exhausted"):
        service.tune_registration(tuning(), director_decision="director_too_late")


def test_delegation_validation_and_immutable_adjustment_history(tmp_path):
    service = ResearchService.create(tmp_path / "study", "Scientific target")
    service.manifest.pop("registration_policy")  # A pre-existing study has no implicit delegation.
    put(service.root / "research.json", service.manifest)
    with pytest.raises(ValueError, match="not delegated"):
        service.tune_registration(tuning(), director_decision="director_one")
    service.configure_registration_policy(
        director_can_tune=True, reason="Operator authorized tuning"
    )
    with pytest.raises(ValueError, match="immutable"):
        service.configure_registration_policy(
            director_can_tune=False, reason="Different delegation"
        )
    grant = service.tune_registration(tuning(), director_decision="director_one")
    assert service.tune_registration(tuning(), director_decision="director_one") == grant
    with pytest.raises(ValueError, match="different registration"):
        service.tune_registration(tuning(180), director_decision="director_one")
    assert not hasattr(Lab, "tune_registration")
    assert not hasattr(Lab, "configure_registration_policy")
    for bad in [tuning(0), tuning(float("inf")), tuning(attempts=0), tuning(attempts=True)]:
        with pytest.raises(ValueError):
            service.tune_registration(bad, director_decision="director_invalid")
    service.tune_registration(tuning(45, 1), director_decision="director_reduce")
    assert ResearchService(service.root).registration_limits() == dict(
        timeout_seconds=45, max_attempts=1
    )
    grant["after"]["timeout_seconds"] = 999
    put(service.root / "registration_adjustments" / (grant["id"] + ".json"), grant)
    with pytest.raises(ValueError, match="inconsistent"):
        service.registration_limits()


@pytest.mark.parametrize("delegated", [True, False])
def test_director_tunes_probe_and_wakes_worker_only_when_authorized(
    tmp_path, monkeypatch, delegated
):
    sup = supervisor(tmp_path)
    sup.state["round"] = 0
    sup.service.configure_registration_policy(
        director_can_tune=delegated, reason="Explicit operator registration setting"
    )
    def failed(method, **kwargs):
        raise WorkerTimeout("Registration failed before any experiments could start")

    with pytest.raises(WorkerUnavailable):
        sup.service.probe_registration(
            SimpleNamespace(call=failed), name="candidate", machine="gpu"
        )
    verdict = dict(
        decision="continue", rationale="Recover the measured slow worker inventory probe.",
        scientific_feasibility="limited", budget_feasibility="at_risk", stop_experiments=[],
        next_action="Retry the blocked instrument registration with the recorded limits.",
        registration_tuning=tuning(),
    )

    def launch(directory, prompt, **kwargs):
        assert "registration.recent_attempts" in prompt
        (directory / "response.json").write_text(json.dumps({
            "type": "item.completed",
            "item": {"type": "agent_message", "text": json.dumps(verdict)},
        }) + "\n" + json.dumps({"type": "turn.completed", "usage": {}}) + "\n")
        return 0

    monkeypatch.setattr(sup, "launch", launch)
    signature = sup.director_strategy_signature(sup.director_packet())
    assert sup.run_director(force=True) is delegated
    action = sup.service.director_status()[0]["registration_action"]
    assert action["status"] == ("granted" if delegated else "rejected")
    assert bool(sup.state.get("director_wake_worker")) is delegated
    if delegated:
        assert sup.director_strategy_signature(sup.director_packet()) != signature


def test_probe_failure_brings_director_review_forward(tmp_path):
    sup = supervisor(tmp_path)
    original = sup.director_strategy_signature(sup.director_packet())

    def failed(method, **kwargs):
        raise WorkerTimeout("slow inventory")

    with pytest.raises(WorkerUnavailable):
        sup.service.probe_registration(
            SimpleNamespace(call=failed), name="candidate", machine="gpu"
        )
    assert sup.director_strategy_signature(sup.director_packet()) != original
