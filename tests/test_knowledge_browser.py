"""Use the actual library UI, including deep links and conversation attachments."""

import threading

import pytest

from conjecture_solver.web.application import SimjectureWebApplication
from conjecture_solver.web.server import create_server


def test_knowledge_navigation_supersedes_a_loading_conversation(tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    app = SimjectureWebApplication(runs_root=tmp_path / "artifacts")
    conversation = app.workspace.create({"name": "Slow conversation"})
    server = create_server(app, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    pending = []
    try:
        with playwright.sync_playwright() as runtime:
            browser = runtime.chromium.launch(headless=True)
            page = browser.new_page()
            base = f"http://127.0.0.1:{server.server_port}"
            page.goto(base + "/workspace")
            page.wait_for_function(
                "() => state.token && !document.getElementById('home-model').disabled"
            )
            page.route("**/api/workspace/project?*", lambda route: pending.append(route))
            page.goto(base + f"/workspace#project={conversation['id']}")
            page.wait_for_function("() => state.routing")
            page.locator("#knowledge-nav").click()
            playwright.expect(page).to_have_url(base + "/workspace#knowledge")
            playwright.expect(page.locator("#view-knowledge")).to_be_visible()
            assert len(pending) == 1
            pending.pop().continue_()
            page.wait_for_function("() => !state.routing")
            playwright.expect(page.locator("#view-knowledge")).to_be_visible()
            playwright.expect(page.locator("#view-project")).to_be_hidden()
            playwright.expect(page).to_have_url(base + "/workspace#knowledge")
            browser.close()
    finally:
        server.shutdown()
        server.server_close()


def test_connect_search_preview_scope_and_attach(tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    folder = tmp_path / "reference-library"
    folder.mkdir()
    (folder / "magnetic.md").write_text(
        "# Magnetic diffusion example\nA resistive decay baseline with a fixed boundary.\n"
    )
    app = SimjectureWebApplication(runs_root=tmp_path / "artifacts")
    conversation = app.workspace.create({"name": "Existing research"})
    server = create_server(app, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    errors = []
    try:
        with playwright.sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.on("pageerror", lambda error: errors.append(str(error)))
            base = f"http://127.0.0.1:{server.server_port}"
            page.goto(base + "/workspace#knowledge")
            page.locator("#knowledge-connect").click()
            page.locator("#knowledge-folder-path").fill(str(folder))
            page.locator("#knowledge-folder-name").fill("Reference library")
            page.locator("#knowledge-folder-form button[type=submit]").click()
            playwright.expect(page.locator("#knowledge-sources-panel")).to_be_visible()
            playwright.expect(page.locator("#knowledge-sources")).to_contain_text(
                "Reference library"
            )
            app.workspace.knowledge._thread.join(10)
            page.locator('[data-knowledge-tab="search"]').click()
            page.locator("#knowledge-query").fill("resistive")
            page.locator("#knowledge-search-form").evaluate("(form) => form.requestSubmit()")
            page.locator(".knowledge-result").first.click()
            playwright.expect(page.locator(".knowledge-preview-body")).to_contain_text(
                "fixed boundary"
            )
            citation_url = page.url
            page.reload()
            playwright.expect(page.locator(".knowledge-preview-body")).to_contain_text(
                "fixed boundary"
            )
            with page.expect_download() as download:
                page.get_by_role("link", name="Download original").click()
            assert download.value.suggested_filename == "magnetic.md"
            page.get_by_role("button", name="Discuss this", exact=True).click()
            playwright.expect(page.locator("#home-knowledge-refs")).to_contain_text(
                "Magnetic diffusion"
            )
            assert "Magnetic diffusion" in page.locator("#first-request").input_value()
            assert len(app.workspace.projects()) == 1  # Preparing a question never launches a turn.
            page.locator("#home-knowledge-picker").click()
            playwright.expect(page.locator("#knowledge-selection-dialog")).to_be_visible()
            page.locator("#knowledge-inherit").uncheck()
            for checkbox in page.locator("#knowledge-selection-list input").all():
                checkbox.uncheck()
            page.locator("#knowledge-save-selection").click()
            playwright.expect(page.locator("#home-knowledge-picker")).to_contain_text("0 sources")
            page.goto(base + f"/workspace#project={conversation['id']}")
            playwright.expect(page.locator("#project-title")).to_have_text("Existing research")
            page.locator("#knowledge-nav").click()
            page.goto(citation_url)
            playwright.expect(page.locator(".knowledge-preview-body")).to_contain_text(
                "fixed boundary"
            )
            # A fresh page has no retained conversation; explicitly navigate then return.
            page.goto(base + f"/workspace#project={conversation['id']}")
            page.locator("#knowledge-nav").click()
            page.locator("#knowledge-query").fill("resistive")
            page.locator("#knowledge-search-form").evaluate("(form) => form.requestSubmit()")
            page.locator(".knowledge-result").first.click()
            page.get_by_role("button", name="Use in conversation", exact=True).click()
            playwright.expect(page.locator("#conversation-knowledge-refs")).to_contain_text(
                "Magnetic diffusion"
            )
            assert list((app.workspace.directory(conversation["id"]) / "turns").iterdir()) == []
            page.locator("#conversation-knowledge-picker").click()
            playwright.expect(page.locator("#knowledge-selection-dialog")).to_be_visible()
            page.locator("#knowledge-inherit").uncheck()
            for checkbox in page.locator("#knowledge-selection-list input").all():
                checkbox.uncheck()
            page.locator("#knowledge-save-selection").click()
            playwright.expect(page.locator("#conversation-knowledge-picker")).to_contain_text(
                "0 sources"
            )
            assert app.workspace.project(conversation["id"])["knowledge_sources"] == []
            page.locator("#knowledge-nav").click()
            page.locator('[data-knowledge-tab="access"]').click()
            playwright.expect(page.locator("#knowledge-mcp-config")).to_contain_text(
                "simjecture-knowledge"
            )
            page.locator('[data-knowledge-tab="search"]').click()
            for width in (1440, 390):
                page.set_viewport_size({"width": width, "height": 900})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            assert not errors
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
