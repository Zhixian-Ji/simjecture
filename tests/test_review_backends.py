"""Mixed-agent launch contracts; no provider requests or scientific solvers."""

import os
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from conjecture_solver.research_service import ResearchService
from conjecture_solver.study_launch import resume_native
from conjecture_solver.web.application import SimjectureWebApplication
from conjecture_solver.web.workspace import load


@pytest.fixture
def setup(tmp_path, monkeypatch):
    from conjecture_solver import execution, mvp_launch

    binary = tmp_path / "bin"
    binary.mkdir()
    codex = binary / "codex"
    codex.write_text("#!/bin/sh\nexit 1\n")
    codex.chmod(0o700)
    monkeypatch.setenv("PATH", str(binary) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setattr(execution, "require_execution_backend", lambda _: {"available": True})
    monkeypatch.setattr(
        mvp_launch, "start_managed_campaign", lambda _: SimpleNamespace(close=lambda: None)
    )
    app = SimjectureWebApplication(runs_root=tmp_path, scan_roots=(tmp_path,))
    workspace = app.workspace
    workspace.__dict__["execution"] = {"backend": "process-cooperative", "available": True}
    workspace.save_api({"base_url": "https://example.invalid/v1", "api_key": "fixture-key"})
    monkeypatch.setattr(workspace, "models", lambda _: {
        "models": [{"id": "worker-fixture"}], "default": "worker-fixture", "note": "Fixture",
    })
    project = workspace.create({"name": "Example research study"})
    workspace.save_brief(project["id"], {
        "question": "Does the energy diagnostic stay within 1% under timestep refinement?",
        "success_criteria": "Compare recorded diagnostics under numerical controls.",
        "constraints": "UI and launch fixture only; no simulation is submitted.",
        "hours": 1, "capability_directory": "",
    })
    return app, workspace, project["id"]


@pytest.mark.parametrize("worker,reviewer", [
    ("builtin", "codex"), ("codex", "builtin"), ("builtin", None), ("codex", None),
])
def test_workspace_freezes_and_resumes_independent_review_connection(setup, worker, reviewer):
    app, workspace, project = setup
    workspace.select_agent(project, {"backend": worker, "model": "worker-fixture"})
    launched = workspace.launch(project, {
        "request_key": "mixed-backends", "judge_backend": reviewer,
        "judge_model": "review-fixture", "judge_reasoning_effort": "high",
    }, app)
    root = Path(launched["path"])
    request = load(root / "study-launch.json")["request"]
    assert request["backend"] == worker and request["judge_backend"] == reviewer
    assert request["judge_model"] == "review-fixture"
    assert "fixture-key" not in (root / "study-launch.json").read_text()
    if (reviewer or worker) == "builtin":
        provider = Path(request["judge_provider_config"])
        assert load(provider)["api_key"] == "fixture-key"
        assert load(provider)["model"] == "review-fixture"
        assert load(provider)["reasoning_effort"] == "high"
        assert provider.stat().st_mode & 0o777 == 0o600
        if worker != "builtin":
            assert request["provider_config"] is None
    if (reviewer or worker) != "builtin":
        assert request["judge_provider_config"] is None
    if worker == "builtin":
        assert load(request["provider_config"])["api_key"] == "fixture-key"
    deadline = ResearchService(root).manifest["deadline"]
    resumed = resume_native(root)
    if reviewer:
        assert resumed.argv[resumed.argv.index("--judge-backend") + 1] == reviewer
    else:
        assert "--judge-backend" not in resumed.argv
    assert ResearchService(root).manifest["deadline"] == deadline


def test_workspace_requires_a_model_for_a_different_review_backend(setup):
    app, workspace, project = setup
    workspace.select_agent(project, {"backend": "builtin", "model": "worker-fixture"})
    with pytest.raises(ValueError, match="model for the separate reviewer"):
        workspace.launch(project, {"request_key": "invalid", "judge_backend": "codex"}, app)
    assert workspace.project(project)["studies"] == []


def test_browser_selects_a_native_reviewer_for_an_api_worker(setup):
    playwright = pytest.importorskip("playwright.sync_api")
    from conjecture_solver.web.server import create_server

    app, workspace, project = setup
    workspace.select_agent(project, {"backend": "builtin", "model": "worker-fixture"})
    server = create_server(app, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    screenshots = Path(__file__).resolve().parents[1] / "artifacts/review-backend-pr"
    screenshots.mkdir(parents=True, exist_ok=True)
    errors = []
    try:
        with playwright.sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"http://127.0.0.1:{server.server_port}/workspace#project={project}&view=autonomous")
            page.locator("#brief-editor > summary").click()
            page.locator("#brief-form .advanced > summary").click()
            page.locator("#review-backend").select_option("codex")
            page.locator("#review-model").fill("review-fixture")
            page.locator("#review-effort").select_option("high")
            page.locator("#review-model").blur()
            page.locator("#review-agent-settings").screenshot(path=str(screenshots / "desktop.png"))
            page.set_viewport_size({"width": 390, "height": 844})
            page.locator("#review-agent-settings").screenshot(path=str(screenshots / "narrow.png"))
            page.get_by_role("button", name="Start research ↗", exact=True).click()
            page.locator(".study-card").first.wait_for()
            saved = workspace.project(project)["studies"][-1]
            request = load(Path(saved["path"]) / "study-launch.json")["request"]
            assert request["backend"] == "builtin" and request["judge_backend"] == "codex"
            assert request["judge_model"] == "review-fixture"
            assert request["judge_reasoning_effort"] == "high"
            assert not errors
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
