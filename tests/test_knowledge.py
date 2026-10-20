"""Knowledge lifecycle, provenance, scope and real transport integration."""

import asyncio
import json
import shutil
import sys
import threading
import zipfile

import httpx
import pytest

from conjecture_solver.knowledge_extract import extract
from conjecture_solver.research_service import put
from conjecture_solver.web.application import SimjectureWebApplication
from conjecture_solver.web.server import create_server
from conjecture_solver.workspace_projects import WorkspaceDirectory


@pytest.fixture
def library(tmp_path):
    spaces = WorkspaceDirectory(tmp_path / "artifacts")
    workspace = spaces.open()
    folder = tmp_path / "sources"
    folder.mkdir()
    (folder / "reference.md").write_text(
        "# Radiation baseline\nCooling before stagnation. 磁重联实验\n"
    )
    source = workspace.knowledge.add({"path": str(folder)})
    workspace.knowledge._thread.join(10)
    return spaces, workspace, folder, source


def hit(knowledge, query="stagnation", **kwargs):
    return knowledge.search(query, **kwargs)["results"][0]


def test_incremental_edit_move_delete_and_preserved_citations(library):
    _, workspace, folder, source = library
    knowledge = workspace.knowledge
    first = hit(knowledge)
    knowledge.sync(force=True)
    assert hit(knowledge)["indexed"] == first["indexed"]
    (folder / "reference.md").write_text("# Revised baseline\nCompression after implosion.\n")
    with pytest.raises(ValueError, match="changed"):
        knowledge.original(first["id"], first["revision"])
    knowledge.sync(force=True)
    assert not knowledge.search("stagnation")["results"]
    revised = hit(knowledge, "implosion")
    assert revised["id"] == first["id"]
    historical = knowledge.read(first["id"], revision=first["revision"])
    assert "Cooling" in historical["text"] and historical["current"] is False
    assert historical["title"] == "Radiation baseline"
    (folder / "reference.md").rename(folder / "renamed.md")
    knowledge.sync(force=True)
    moved = hit(knowledge, "implosion")
    assert moved["id"] == first["id"]
    assert moved["path"] == "renamed.md"
    (folder / "renamed.md").unlink()
    knowledge.sync(force=True)
    assert not knowledge.search("implosion")["results"]
    assert knowledge.read(first["id"], revision=first["revision"])["current"] is False


def test_scope_selection_and_shared_source_registration(library):
    spaces, workspace, folder, source = library
    spaces.create({"name": "Other lab", "id": "lab"})
    other = spaces.open("lab").knowledge
    first = hit(workspace.knowledge)
    assert not other.search("stagnation")["results"]
    with pytest.raises(ValueError, match="not available"):
        other.read(first["id"])
    attached = other.add({"path": str(folder)})
    other._thread.join(10)
    assert attached["id"] == source["id"]
    assert hit(other)["id"] == first["id"]
    assert "workspace=lab" in hit(other)["url"]
    conversation = workspace.create({"name": "No knowledge"})
    workspace.knowledge.set_selection(conversation["id"], [])
    assert not workspace.knowledge.search("stagnation", conversation=conversation["id"])["results"]
    with pytest.raises(ValueError, match="not available"):
        workspace.knowledge.read(first["id"], conversation=conversation["id"])
    workspace.knowledge.configure({"id": source["id"], "enabled": False})
    with pytest.raises(ValueError, match="not available"):
        workspace.knowledge.read(first["id"])
    assert hit(other)["id"] == first["id"]


def test_exclusions_symlinks_failure_and_offline_source(library, tmp_path):
    _, workspace, folder, source = library
    knowledge = workspace.knowledge
    for directory in (".private", "build", "node_modules"):
        (folder / directory).mkdir()
        (folder / directory / "private.md").write_text("do_not_index")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private.md").write_text("do_not_index")
    (folder / "linked").symlink_to(outside, target_is_directory=True)
    (folder / "secret.json").write_text('{"value":"do_not_index"}')
    knowledge.sync(force=True)
    assert not knowledge.search("do_not_index")["results"]
    before = hit(knowledge)
    offline = folder.with_name("offline")
    folder.rename(offline)
    knowledge.sync(force=True)
    assert knowledge._source(source["id"])["state"] == "unavailable"
    assert hit(knowledge)["id"] == before["id"]
    offline.rename(folder)
    (folder / "reference.md").write_bytes(b"\x00binary")
    knowledge.sync(force=True)
    assert not knowledge.search("stagnation")["results"]
    assert knowledge._source(source["id"])["state"] == "attention"
    (folder / "reference.md").write_text("stagnation recovered")
    knowledge.sync(force=True)
    assert hit(knowledge)["id"] == before["id"]


def test_shared_folder_keeps_distinct_exclusions(library):
    spaces, workspace, folder, original = library
    (folder / "omit.txt").write_text("excluded sentinel content")
    workspace.knowledge.sync(force=True)
    assert hit(workspace.knowledge, "sentinel")["path"] == "omit.txt"
    spaces.create({"name": "Filtered workspace", "id": "filtered"})
    filtered = spaces.open("filtered").knowledge
    source = filtered.add({"path": str(folder), "excludes": ["omit.txt"]})
    filtered._thread.join(10)
    assert source["id"] != original["id"]
    assert not filtered.search("sentinel")["results"]
    assert hit(filtered)["path"] == "reference.md"
    assert hit(workspace.knowledge, "sentinel")["path"] == "omit.txt"
    spaces.create({"name": "Same filter", "id": "same-filter"})
    same = spaces.open("same-filter").knowledge
    attached = same.add({"path": str(folder), "excludes": ["omit.txt", "omit.txt"]})
    same._thread.join(10)
    assert attached["id"] == source["id"]
    assert not same.search("sentinel")["results"]


def test_cached_extraction_keeps_each_files_title(library):
    _, workspace, folder, _ = library
    (folder / "alpha.txt").write_text("identical content without a heading")
    (folder / "beta.txt").write_text("identical content without a heading")
    workspace.knowledge.sync(force=True)
    alpha = hit(workspace.knowledge, "alpha")
    beta = hit(workspace.knowledge, "beta")
    assert alpha["title"] == "alpha"
    assert beta["title"] == "beta"
    assert alpha["revision"] == beta["revision"]
    assert workspace.knowledge.read(beta["id"])["title"] == "beta"


def test_structured_conversation_memory_and_deletion(library):
    _, workspace, _, _ = library
    conversation = workspace.create({"name": "Boundary conditions"})
    directory = workspace.directory(conversation["id"])
    turn = directory / "turns" / "1234567890000000000"
    turn.mkdir()
    put(turn / "request.json", {"message": "Why reject periodic walls?", "created_at": 1234567890})
    workspace.knowledge.sync(force=True)
    assert not workspace.knowledge.search("periodic", kind="conversations")["results"]
    (turn / "events.jsonl").write_text(
        json.dumps(
            {"type": "result", "result": "Periodic walls produced an unphysical return flow."}
        )
        + "\n"
    )
    workspace.knowledge.sync(force=True)
    result = hit(workspace.knowledge, "periodic", kind="conversations")
    source = workspace.knowledge.read(result["id"])
    assert source["conversation"] == conversation["id"]
    assert "User:" in source["text"] and "Assistant:" in source["text"]
    assert source["evidence_status"] == "context_only"
    shutil.rmtree(directory)
    workspace.knowledge.forget_conversation(conversation["id"])
    assert not workspace.knowledge.search("periodic", kind="conversations")["results"]
    with pytest.raises(ValueError):
        workspace.knowledge.read(result["id"], revision=result["revision"])


def test_source_selection_and_frozen_context_reach_agent(library, monkeypatch):
    from conjecture_solver.web import workspace as module

    _, workspace, folder, source = library
    conversation = workspace.create({"name": "Use reference", "knowledge_sources": [source["id"]]})
    monkeypatch.setattr(
        workspace,
        "project_connection",
        lambda _: {"backend": "builtin", "model": "fixture", "api_key": "fixture"},
    )
    monkeypatch.setattr(workspace, "inventory_context", lambda: "")
    monkeypatch.setattr(module, "spawn", lambda *args: {})
    result = hit(workspace.knowledge)
    workspace.send(
        conversation["id"], {"message": "Use this reference", "knowledge_refs": [result]}
    )
    directory = workspace.directory(conversation["id"])
    turn = next((directory / "turns").iterdir())
    prompt = (turn / "prompt.txt").read_text()
    assert "Cooling before stagnation" in prompt
    assert "knowledge_search" in prompt
    receipt = next((turn / "knowledge-context").glob("*.json"))
    saved = receipt.read_bytes()
    (folder / "reference.md").write_text("A revised source")
    workspace.knowledge.sync(force=True)
    assert receipt.read_bytes() == saved
    study_context = directory / "study-context"
    assert workspace.knowledge.freeze_references(conversation["id"], study_context) == 1
    assert "Cooling before stagnation" in (study_context / "references.json").read_text()
    assert (
        workspace.project(conversation["id"])["messages"][-1]["knowledge_citations"][0]["revision"]
        == result["revision"]
    )


def test_slide_order_and_speaker_notes(tmp_path):
    path = tmp_path / "talk.pptx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "ppt/presentation.xml",
            '<p:presentation xmlns:p="p" xmlns:r="r"><p:sldIdLst>'
            '<p:sldId id="9" r:id="two"/><p:sldId id="8" r:id="one"/>'
            "</p:sldIdLst></p:presentation>",
        )
        archive.writestr(
            "ppt/_rels/presentation.xml.rels",
            '<Relationships><Relationship Id="one" Target="slides/slide1.xml"/>'
            '<Relationship Id="two" Target="slides/slide2.xml"/></Relationships>',
        )
        archive.writestr("ppt/slides/slide1.xml", "<root><p><t>Second slide</t></p></root>")
        archive.writestr("ppt/slides/slide2.xml", "<root><p><t>First slide</t></p></root>")
        archive.writestr(
            "ppt/slides/_rels/slide2.xml.rels",
            '<Relationships><Relationship Id="note" Type="example/notesSlide" '
            'Target="../notesSlides/notesSlide1.xml"/></Relationships>',
        )
        archive.writestr(
            "ppt/notesSlides/notesSlide1.xml",
            "<root><p><t>Explain the magnetic topology.</t></p></root>",
        )
    parsed = extract(path)
    assert parsed["kind"] == "slides"
    assert "First slide" in parsed["sections"][0]["text"]
    assert "magnetic topology" in parsed["sections"][0]["text"]
    assert parsed["sections"][0]["location"].startswith("Slide 1")
    assert "Second slide" in parsed["sections"][1]["text"]


def test_pdf_page_citation_and_chinese_search(library):
    pytest.importorskip("pypdf")
    import matplotlib.pyplot as plt

    _, workspace, folder, _ = library
    fig = plt.figure()
    fig.text(0.1, 0.5, "Magnetic diffusion reference")
    fig.savefig(folder / "paper.pdf")
    plt.close(fig)
    workspace.knowledge.sync(force=True)
    result = hit(workspace.knowledge, "diffusion", kind="papers")
    assert workspace.knowledge.read(result["id"])["location"].startswith("Page 1")
    assert hit(workspace.knowledge, "磁重联")["kind"] == "documents"


def test_pdf_surrogates_are_reported_and_do_not_break_json(tmp_path, monkeypatch):
    pypdf = pytest.importorskip("pypdf")
    from types import SimpleNamespace

    monkeypatch.setattr(
        pypdf,
        "PdfReader",
        lambda _: SimpleNamespace(
            is_encrypted=False,
            pages=[SimpleNamespace(extract_text=lambda: "Magnetic \udcfe field")],
        ),
    )
    result = extract(tmp_path / "malformed-font.pdf")
    assert "could not be decoded" in result["warning"]
    assert result["sections"][0]["text"] == "Magnetic ? field"
    json.dumps(result, ensure_ascii=False).encode("utf-8")


def test_http_tokens_scope_readonly_and_download(library):
    spaces, workspace, _, source = library
    app = SimjectureWebApplication(runs_root=spaces.root)
    server = create_server(app, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with httpx.Client(
            base_url=f"http://127.0.0.1:{server.server_port}", trust_env=False
        ) as client:
            boot = client.get("/api/workspace/bootstrap").json()
            assert boot["knowledge_available"]
            result = client.get("/api/workspace/knowledge-search?q=stagnation").json()["results"][0]
            params = {"id": result["id"], "revision": result["revision"]}
            assert client.get(
                "/api/workspace/knowledge-original", params=params
            ).content.startswith(b"# Radiation")
            assert client.post("/api/workspace/knowledge-refresh", json={}).status_code == 403
            app.allow_mutations = False
            assert (
                client.post(
                    "/api/workspace/knowledge-refresh",
                    json={},
                    headers={"X-Simjecture-Token": boot["control_token"]},
                ).status_code
                == 403
            )
            assert client.get("/api/workspace/knowledge-read", params=params).status_code == 200
            assert (
                "simjecture-knowledge"
                in client.get("/api/workspace/knowledge-access").json()["mcp"]["mcpServers"]
            )
            assert client.get("/assets/workspace-knowledge.js").status_code == 200
    finally:
        server.shutdown()
        server.server_close()


def test_real_mcp_search_and_read(library):
    pytest.importorskip("mcp")
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    spaces, _, _, _ = library
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "conjecture_solver.cli",
            "knowledge",
            "--runs-root",
            str(spaces.root),
            "mcp",
        ],
    )

    async def exercise():
        async with (
            stdio_client(parameters) as (reader, writer),
            ClientSession(reader, writer) as session,
        ):
            await session.initialize()
            listing = await session.list_tools()
            assert {t.name for t in listing.tools} == {
                "knowledge_search",
                "knowledge_read",
                "knowledge_status",
            }
            response = await session.call_tool("knowledge_search", {"query": "stagnation"})
            assert not getattr(response, "is_error", getattr(response, "isError", False))
            data = json.loads(response.content[0].text)
            item = data["results"][0]
            response = await session.call_tool(
                "knowledge_read", {"id": item["id"], "revision": item["revision"]}
            )
            assert not getattr(response, "is_error", getattr(response, "isError", False))
            assert "Cooling" in response.content[0].text

    asyncio.run(exercise())
