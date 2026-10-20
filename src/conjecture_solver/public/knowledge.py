"""Private hosted conversation retrieval, authorized against live ownership on every read."""

import json
import time
from urllib.parse import urlencode

from ..knowledge import STOP_WORDS, digest, tokens
from ..knowledge_extract import chunks
from .organization import Organization
from .store import TERMINAL


class HostedKnowledge:
    def __init__(self, store, owner, workspace="personal"):
        self.store, self.owner = store, owner
        self.organization = Organization(store, owner, workspace)
        self.organization.require_member()
        self.workspace = workspace
        self.source = digest(self.organization.tenant + ":" + workspace + ":conversations")[:24]

    def _scope(self):
        return (
            "p.deleting=0 AND p.workspace=? AND " + self.store.owner_clause("p.owner"),
            (self.workspace, self.owner, self.owner),
        )

    def sync(self):
        scope, parameters = self._scope()
        with self.store.connect(write=True) as db:
            rows = db.execute(
                "SELECT m.id,m.project,m.content,m.created,p.name,j.hypothesis "
                "FROM messages m JOIN projects p ON p.id=m.project JOIN jobs j ON j.id=m.job "
                f"WHERE {scope} AND m.role='assistant' AND j.deleted=0 AND j.status IN ("
                + ",".join("?" for _ in TERMINAL)
                + ") ORDER BY m.id DESC LIMIT 5000",
                (*parameters, *sorted(TERMINAL)),
            ).fetchall()
            retained = [row["id"] for row in rows]
            db.execute(
                "DELETE FROM knowledge_passages WHERE project IN "
                f"(SELECT p.id FROM projects p WHERE {scope})"
                + (
                    " AND message NOT IN (" + ",".join("?" for _ in retained) + ")"
                    if retained
                    else ""
                ),
                (*parameters, *retained),
            )
            for row in rows:
                document = digest(
                    "hosted-conversation:" + row["project"] + ":" + str(row["id"])
                )[:32]
                body = "User:\n" + row["hypothesis"] + "\n\nAssistant:\n" + row["content"]
                revision = digest(row["name"] + "\n" + body)
                previous = db.execute(
                    "SELECT revision FROM knowledge_passages WHERE document=? LIMIT 1",
                    (document,),
                ).fetchone()
                if previous and previous["revision"] == revision:
                    continue
                db.execute("DELETE FROM knowledge_passages WHERE document=?", (document,))
                excerpt = body[:200_000]
                if len(body) > len(excerpt):
                    excerpt += "\n[Indexed excerpt: this turn exceeds 200,000 characters.]"
                for ordinal, section in enumerate(chunks(excerpt)):
                    db.execute(
                        "INSERT INTO knowledge_passages(document,project,message,revision,ordinal,"
                        "title,body,created,indexed) VALUES(?,?,?,?,?,?,?,?,?)",
                        (
                            document,
                            row["project"],
                            row["id"],
                            revision,
                            ordinal,
                            row["name"],
                            section["text"],
                            row["created"],
                            time.time(),
                        ),
                    )

    def status(self):
        enabled = self.organization.require_workspace()["knowledge_enabled"]
        if enabled:
            self.sync()
        scope, parameters = self._scope()
        with self.store.connect() as db:
            count, indexed = db.execute(
                "SELECT count(DISTINCT k.document),max(k.indexed) FROM knowledge_passages k "
                f"JOIN projects p ON p.id=k.project WHERE {scope}",
                parameters,
            ).fetchone()
        return {
            "sources": [
                {
                    "id": self.source,
                    "name": "Workspace conversations",
                    "kind": "conversations",
                    "path": "Saved conversations",
                    "enabled": bool(enabled),
                    "state": "ready",
                    "checked": indexed or 0,
                    "indexed": indexed or 0,
                    "documents": count,
                    "excludes": [],
                    "issues": [],
                    "progress": count,
                    "error": "",
                }
            ],
            "scope": self.workspace,
            "search_mode": "local_full_text",
            "refresh_seconds": 60,
            "formats": "Saved conversation turns",
        }

    def allowed(self, conversation=None, selection=None):
        enabled = bool(self.organization.require_workspace()["knowledge_enabled"])
        if conversation:
            project = self.organization.conversation(conversation)
            configured = (
                json.loads(project["knowledge_sources"]) if project["knowledge_sources"] else None
            )
            enabled = enabled and (configured is None or self.source in configured)
        available = [self.source] if enabled else []
        if selection is not None:
            if not isinstance(selection, list) or any(s not in available for s in selection):
                raise ValueError("Sources are disabled or outside this workspace")
            available = list(dict.fromkeys(selection))
        return available

    def configure(self, values):
        if set(values) != {"id", "enabled"} or values["id"] != self.source:
            raise ValueError("Choose this workspace's conversation source")
        if not isinstance(values["enabled"], bool):
            raise ValueError("Enabled must be boolean")
        with self.store.connect(write=True) as db:
            db.execute(
                "UPDATE hosted_workspaces SET knowledge_enabled=? WHERE tenant=? AND id=?",
                (int(values["enabled"]), self.organization.tenant, self.workspace),
            )
        return self.status()

    def select(self, conversation, selection):
        self.organization.conversation(conversation)
        if selection is not None:
            self.allowed(selection=selection)
        with self.store.connect(write=True) as db:
            if db.execute(
                "SELECT 1 FROM jobs WHERE project=? AND status IN ('queued','running')",
                (conversation,),
            ).fetchone():
                raise ValueError("Wait for the current task before changing knowledge sources")
            db.execute(
                "UPDATE projects SET knowledge_sources=? WHERE id=?",
                (json.dumps(selection) if selection is not None else None, conversation),
            )
        return {"knowledge_sources": selection}

    def link(self, document, revision, chunk):
        space = (
            "?" + urlencode({"workspace": self.workspace}) if self.workspace != "personal" else ""
        )
        return (
            "/workspace"
            + space
            + "#"
            + urlencode({"knowledge": document, "revision": revision, "chunk": chunk})
        )

    def search(self, query="", *, kind="all", source=None, conversation=None, limit=30):
        if not isinstance(query, str) or len(query) > 1000:
            raise ValueError("Search must be at most 1,000 characters")
        available = self.allowed(conversation, [source] if source else None)
        result = {"results": [], "query": query, "search_mode": "local_full_text"}
        if not available or kind not in {"all", "conversations"}:
            return result
        self.sync()
        scope, parameters = self._scope()
        words = list(dict.fromkeys(t for t in tokens(query) if t not in STOP_WORDS))[:24]
        with self.store.connect() as db:
            if words:
                match = " OR ".join('"' + word + '"' for word in words)
                rows = db.execute(
                    "SELECT k.*,bm25(hosted_search,5,1) AS rank FROM hosted_search "
                    "JOIN knowledge_passages k ON k.id=hosted_search.rowid "
                    "JOIN projects p ON p.id=k.project "
                    f"WHERE hosted_search MATCH ? AND {scope} ORDER BY rank LIMIT 250",
                    (match, *parameters),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT k.* FROM knowledge_passages k JOIN projects p ON p.id=k.project "
                    f"WHERE {scope} AND k.ordinal=0 ORDER BY k.created DESC LIMIT 50",
                    parameters,
                ).fetchall()
        seen = set()
        for row in rows:
            if row["document"] in seen:
                continue
            seen.add(row["document"])
            value = self._projection(row)
            offset = max(
                0,
                min(
                    [row["body"].lower().find(w) for w in words if w in row["body"].lower()],
                    default=0,
                )
                - 100,
            )
            value.update(snippet=row["body"][offset : offset + 380])
            result["results"].append(value)
            if len(result["results"]) >= max(1, min(30, int(limit))):
                break
        return result

    def _projection(self, row):
        return {
            "id": row["document"],
            "source": self.source,
            "source_name": "Workspace conversations",
            "source_state": "ready",
            "title": row["title"],
            "kind": "conversations",
            "path": row["title"],
            "revision": row["revision"],
            "chunk": row["ordinal"],
            "location": f"Conversation turn {row['message']} · section {row['ordinal'] + 1}",
            "indexed": row["indexed"],
            "conversation": row["project"],
            "turn": row["message"],
            "url": self.link(row["document"], row["revision"], row["ordinal"]),
            "evidence_status": "context_only",
        }

    def read(self, document, *, revision=None, chunk=0, conversation=None):
        if not self.allowed(conversation):
            raise ValueError("Knowledge is disabled for this conversation")
        self.sync()
        scope, parameters = self._scope()
        with self.store.connect() as db:
            rows = db.execute(
                "SELECT k.* FROM knowledge_passages k JOIN projects p ON p.id=k.project "
                f"WHERE k.document=? AND {scope} ORDER BY k.ordinal",
                (document, *parameters),
            ).fetchall()
        if not rows or (revision and revision != rows[0]["revision"]):
            raise LookupError("Knowledge passage is unavailable in this workspace")
        chunk = int(chunk)
        if chunk < 0 or chunk >= len(rows):
            raise ValueError("Source section is out of range")
        row = rows[chunk]
        return self._projection(row) | {
            "text": row["body"],
            "chunks": len(rows),
            "current": True,
            "downloadable": False,
            "warning": "Saved conversation context; verify claims against original evidence.",
        }

    def references(self, conversation, references):
        if not isinstance(references, list) or len(references) > 8:
            raise ValueError("Choose at most eight reference passages")
        passages = []
        for reference in references:
            if (
                not isinstance(reference, dict)
                or not isinstance(reference.get("id"), str)
                or not isinstance(reference.get("revision"), str)
                or not 1 <= len(reference["id"]) <= 100
                or not 1 <= len(reference["revision"]) <= 100
            ):
                raise ValueError("A reference needs a document and exact revision")
            if type(reference.get("chunk", 0)) is not int:
                raise ValueError("Reference section must be an integer")
            passages.append(
                self.read(
                    reference["id"],
                    revision=reference["revision"],
                    chunk=reference.get("chunk", 0),
                    conversation=conversation,
                )
            )
        return passages


def agent_tools(store, job):
    """Bind retrieval to the job's verified owner and conversation, never model-supplied paths."""
    if not job.get("project") or not store.account(job["owner"]):
        return []
    from smolagents import tool

    project = store.project(job["project"], job["owner"])
    if not project:
        return []
    library = HostedKnowledge(store, job["owner"], project["workspace"])

    @tool
    def knowledge_search(query: str) -> str:
        """Search completed conversations in this workspace as reference context.

        Args:
            query: Words or a distinctive phrase to retrieve.
        """
        return json.dumps(library.search(query, conversation=project["id"]), ensure_ascii=False)

    @tool
    def knowledge_read(document: str, revision: str, chunk: int = 0) -> str:
        """Read an exact cited conversation passage; this is not new scientific evidence.

        Args:
            document: Document ID returned by knowledge_search.
            revision: Exact revision returned by knowledge_search.
            chunk: Section number returned by knowledge_search.
        """
        result = library.read(document, revision=revision, chunk=chunk, conversation=project["id"])
        store.event(
            job["id"],
            "knowledge_read",
            {
                "document": result["id"],
                "revision": result["revision"],
                "chunk": result["chunk"],
                "title": result["title"],
                "url": result["url"],
                "location": result["location"],
            },
        )
        return json.dumps(result, ensure_ascii=False)

    return [knowledge_search, knowledge_read]
