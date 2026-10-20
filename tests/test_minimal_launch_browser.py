"""Exercise the simplified form against the real HTTP launch endpoint."""

import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from conjecture_solver.study_launch import materialize_native
from conjecture_solver.web.application import SimjectureWebApplication
from conjecture_solver.web.server import create_server


@pytest.mark.parametrize("width", [1440, 390])
def test_minimal_launch_form_posts_a_valid_contract(tmp_path, monkeypatch, width):
    playwright = pytest.importorskip("playwright.sync_api")
    app = SimjectureWebApplication(runs_root=tmp_path / "runs", scan_roots=(tmp_path,))
    requests = []

    def materialize(request):
        requests.append(request)
        return materialize_native(
            request.model_copy(
                update={
                    "agent_executable": sys.executable,
                    "capability_directory": None,
                    "execution_backend": "process-cooperative",
                }
            )
        )

    monkeypatch.setattr("conjecture_solver.web.application.materialize_operator_input", materialize)
    monkeypatch.setattr(
        "conjecture_solver.web.application.start_managed_campaign",
        lambda plan: SimpleNamespace(identity=SimpleNamespace(pid=123), close=lambda: None),
    )
    server = create_server(app, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    errors = []
    try:
        with playwright.sync_playwright() as runtime:
            browser = runtime.chromium.launch(headless=True)
            page = browser.new_page(
                viewport={"width": width, "height": 1000 if width > 400 else 844}
            )
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"http://127.0.0.1:{server.server_port}/monitor")
            page.locator("#new-run-button").click()
            form = page.locator("#new-run-form")
            playwright.expect(form).to_be_visible()
            assert form.locator('select[name="mode"]').count() == 0
            assert form.locator("#launch-backend option").all_text_contents() == [
                "Codex GLM",
                "Codex",
                "Grok",
                "AGY",
            ]
            assert form.locator('[name="max_workspace_mb"],[name="max_memory_mb"]').count() == 0
            form.locator("#launch-backend").select_option("codex")
            form.locator("#launch-model").fill("fixture-model")
            form.locator('[name="hypothesis"]').fill(
                "A finite test of the minimal launch contract."
            )
            form.locator('[name="instruction"]').fill(
                "Synthetic interface check; no agent will run."
            )
            form.locator('[name="campaign_id"]').fill("minimal-browser")
            screenshots = Path("artifacts/pr-review")
            screenshots.mkdir(parents=True, exist_ok=True)
            page.locator("#new-run-dialog").evaluate("(dialog) => { dialog.scrollTop = 0; }")
            page.screenshot(path=str(screenshots / f"minimal-launch-{width}.png"), full_page=True)
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            with page.expect_response(
                lambda response: (
                    response.url.endswith("/api/campaigns") and response.request.method == "POST"
                )
            ) as response:
                form.locator("#launch-button").click()
            assert response.value.status == 201, response.value.text()
            playwright.expect(page.locator("#new-run-dialog")).not_to_be_visible()
            assert len(requests) == 1
            assert requests[0].mode == "minimal"
            assert requests[0].backend == "codex"
            assert requests[0].model == "fixture-model"
            assert not errors
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
