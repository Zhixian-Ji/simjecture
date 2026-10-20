"""Real HTTP ownership, workspace, indexing and deletion boundaries."""

import json
import time

import pytest

from conjecture_solver.public.knowledge import agent_tools
from conjecture_solver.public.server import COOKIE, create_app

from .test_public_workspace import session


@pytest.fixture
def app(tmp_path):
    (tmp_path / "provider.json").write_text(
        json.dumps(
            {
                "public_enabled": True,
                "api_key": "fixture-secret",
                "model": "fixture-model",
                "base_url": "https://fixture.invalid",
            }
        )
    )
    return create_app(tmp_path, dispatch=False, secure_cookies=False)


def member(app, identifier=123):
    client = session(app)
    store = app.state.store
    visitor = store.visitor(client.cookies.get(COOKIE))
    token, row = store.authorize_identity(
        visitor["id"], identifier, f"user{identifier}", "Researcher"
    )
    client.cookies.set(COOKIE, token)
    client.headers["X-CSRF-Token"] = row["csrf"]
    return client


def endpoint(name, space="personal"):
    return f"/api/workspace/{name}?workspace={space}"


def create_conversation(client, space="personal", **values):
    result = client.post(endpoint("projects", space), json={"name": "A conversation", **values})
    assert result.status_code == 200, result.text
    return result.json()


def finish_turn(app, client, conversation, space="personal", text="Amber resonance sentinel"):
    response = client.post(
        endpoint("message", space),
        json={"project": conversation["id"], "message": "Remember this."},
    )
    assert response.status_code == 200, response.text
    store = app.state.store
    job = store.claim()
    assert job["id"] == response.json()["id"]
    store.update(job["id"], status="completed", finished=time.time(), summary=text)
    store.finish_project_job(job, text)
    return job


def test_guest_keeps_conversations_but_saved_organization_requires_signin(app):
    guest = session(app)
    p = create_conversation(guest, collection=None, knowledge_sources=None)
    assert p["collection"] is None
    boot = guest.get(endpoint("bootstrap")).json()
    assert not boot["knowledge_available"] and "workspaces" not in boot
    for route, values in [
        ("workspaces", {"name": "Lab"}),
        ("collections", {"name": "Project"}),
        ("knowledge-refresh", {}),
    ]:
        assert guest.post(endpoint(route), json=values).status_code == 403
    assert guest.get(endpoint("knowledge-search") + "&q=private").status_code == 403
    assert guest.get(endpoint("bootstrap", "guessed-space")).status_code == 403


def test_workspaces_collections_and_conversations_are_account_scoped(app):
    first, second = member(app), member(app, 456)
    space = first.post(endpoint("workspaces"), json={"name": "Plasma lab"}).json()["id"]
    collection = first.post(
        endpoint("collections", space),
        json={
            "name": "Diffusion",
            "description": "A bounded project",
            "instructions": "Check boundary flux.",
        },
    ).json()
    conversation = create_conversation(first, space, collection=collection["id"])
    assert first.get(endpoint("bootstrap")).json()["projects"] == []
    boot = first.get(endpoint("bootstrap", space)).json()
    assert boot["projects"][0]["id"] == conversation["id"]
    assert boot["collections"][0]["instructions"] == "Check boundary flux."
    same_account = member(app)
    assert (
        same_account.get(endpoint("bootstrap", space)).json()["projects"][0]["id"]
        == conversation["id"]
    )
    assert first.get(endpoint("project") + "&id=" + conversation["id"]).status_code == 404
    assert second.get(endpoint("bootstrap", space)).status_code == 404
    assert (
        second.post(
            endpoint("collections"),
            json={
                "id": collection["id"],
                "name": "Overwrite",
            },
        ).status_code
        == 404
    )
    assert (
        second.post(
            endpoint("projects"),
            json={
                "name": "Bad reference",
                "collection": collection["id"],
            },
        ).status_code
        == 404
    )
    assert (
        first.post(
            endpoint("assign-collection", space),
            json={
                "project": conversation["id"],
                "collection": None,
            },
        ).json()["collection"]
        is None
    )


def test_completed_turns_are_backfilled_private_and_erased_on_deletion(app):
    first, second = member(app), member(app, 456)
    p = create_conversation(first)
    finish_turn(app, first, p)
    # Index is derived on demand; raw provider events are not sources.
    response = first.get(endpoint("knowledge-search") + "&q=Amber")
    assert response.status_code == 200, response.text
    reference = response.json()["results"][0]
    assert reference["evidence_status"] == "context_only"
    read = endpoint("knowledge-read") + f"&id={reference['id']}&revision={reference['revision']}"
    assert "Amber resonance" in first.get(read).json()["text"]
    assert second.get(endpoint("knowledge-search") + "&q=Amber").json()["results"] == []
    assert second.get(read).status_code == 404
    space = first.post(endpoint("workspaces"), json={"name": "Separate"}).json()["id"]
    assert first.get(endpoint("knowledge-search", space) + "&q=Amber").json()["results"] == []
    assert first.get(read.replace("workspace=personal", "workspace=" + space)).status_code == 404
    fresh = member(app)
    assert fresh.get(read).status_code == 200
    response = first.post(endpoint("delete-project"), json={"project": p["id"], "confirm": p["id"]})
    assert response.status_code == 200
    assert first.get(read).status_code == 404
    assert first.get(endpoint("knowledge-search") + "&q=Amber").json()["results"] == []
    with app.state.store.connect() as db:
        assert db.execute("SELECT count(*) FROM knowledge_passages").fetchone()[0] == 0
        assert (
            db.execute(
                "SELECT count(*) FROM hosted_search WHERE hosted_search MATCH 'Amber'"
            ).fetchone()[0]
            == 0
        )
    replacement = create_conversation(first)
    finish_turn(app, first, replacement)
    newer = first.get(endpoint("knowledge-search") + "&q=Amber").json()["results"][0]
    assert newer["id"] != reference["id"]  # SQLite can reuse deleted message row IDs.
    assert first.get(read).status_code == 404


def test_source_selection_references_and_worker_tools_preserve_scoping(app):
    client = member(app)
    old = create_conversation(client)
    finish_turn(app, client, old)
    reference = client.get(endpoint("knowledge-search") + "&q=Amber").json()["results"][0]
    collection = client.post(
        endpoint("collections"),
        json={
            "name": "Guided project",
            "instructions": "Check boundary flux independently.",
        },
    ).json()
    new = create_conversation(client, collection=collection["id"])
    assert (
        client.post(
            endpoint("knowledge-selection"),
            json={
                "project": new["id"],
                "sources": [],
            },
        ).status_code
        == 200
    )
    request = {
        "project": new["id"],
        "message": "Use the earlier result.",
        "knowledge_refs": [reference],
    }
    assert client.post(endpoint("message"), json=request).status_code == 400
    assert (
        client.post(
            endpoint("knowledge-selection"),
            json={
                "project": new["id"],
                "sources": None,
            },
        ).status_code
        == 200
    )
    response = client.post(endpoint("message"), json=request)
    assert response.status_code == 200, response.text
    job = app.state.store.job(response.json()["id"])
    assert "boundary flux" in job["reference_context"]["project"]["instructions"]
    assert "Amber resonance" in job["reference_context"]["knowledge"][0]["text"]
    tools = {tool.name: tool for tool in agent_tools(app.state.store, job)}
    found = json.loads(tools["knowledge_search"](query="Amber"))["results"]
    assert found[0]["id"] == reference["id"]
    read = json.loads(
        tools["knowledge_read"](
            document=reference["id"],
            revision=reference["revision"],
            chunk=0,
        )
    )
    assert read["evidence_status"] == "context_only"
    assert client.get(endpoint("project") + "&id=" + new["id"]).status_code == 200
    assert any(e["kind"] == "knowledge_read" for e in app.state.store.events(job["id"]))
    assert (
        client.post(
            endpoint("assign-collection"),
            json={
                "project": new["id"],
                "collection": None,
            },
        ).status_code
        == 400
    )
    assert (
        client.post(
            endpoint("knowledge-selection"),
            json={
                "project": new["id"],
                "sources": [],
            },
        ).status_code
        == 400
    )


def test_disabled_sources_stale_revisions_and_host_paths_are_rejected(app):
    client = member(app)
    p = create_conversation(client)
    finish_turn(app, client, p)
    reference = client.get(endpoint("knowledge-search") + "&q=Amber").json()["results"][0]
    assert (
        client.get(
            endpoint("knowledge-read") + "&id=" + reference["id"] + "&revision=bad"
        ).status_code
        == 404
    )
    for route in ["knowledge-source", "knowledge-access", "collection-file"]:
        response = client.post(
            endpoint(route), json={"path": "/srv/simjecture-public/data/provider.json"}
        )
        assert response.status_code == 403
    assert (
        client.post(
            endpoint("knowledge-configure"),
            json={
                "id": reference["source"],
                "enabled": False,
            },
        ).status_code
        == 200
    )
    assert client.get(endpoint("knowledge-search") + "&q=Amber").json()["results"] == []
    assert client.get(endpoint("knowledge-read") + "&id=" + reference["id"]).status_code == 400
    assert (
        client.post(
            endpoint("knowledge-configure"),
            json={
                "id": reference["source"],
                "enabled": True,
            },
        ).status_code
        == 200
    )
    assert client.get(endpoint("knowledge-search") + "&q=Amber").json()["results"]


def test_pending_deletion_hides_index_before_files_are_erased(app, monkeypatch):
    from conjecture_solver.public.deletion import ProjectDeletion

    client = member(app)
    p = create_conversation(client)
    finish_turn(app, client, p)
    reference = client.get(endpoint("knowledge-search") + "&q=Amber").json()["results"][0]
    monkeypatch.setattr(ProjectDeletion, "erase", lambda *_: False)
    response = client.post(
        endpoint("delete-project"),
        json={"project": p["id"], "confirm": p["id"]},
    )
    assert response.json()["pending"]
    assert client.get(endpoint("knowledge-search") + "&q=Amber").json()["results"] == []
    assert client.get(endpoint("knowledge-read") + "&id=" + reference["id"]).status_code == 404
    with app.state.store.connect() as db:
        assert db.execute("SELECT count(*) FROM knowledge_passages").fetchone()[0] > 0
