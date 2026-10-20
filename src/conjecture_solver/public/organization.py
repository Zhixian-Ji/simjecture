"""Account-owned hosted workspaces and projects (collections of conversations)."""

import secrets
import time


def text(value, label, maximum, *, optional=False):
    if not isinstance(value, str) or len(value) > maximum or (not optional and not value.strip()):
        raise ValueError(f"{label} must be {'0' if optional else '1'}–{maximum} characters")
    return value.strip()


class Organization:
    def __init__(self, store, owner, workspace="personal"):
        self.store, self.owner = store, owner
        self.account = store.account(owner)
        self.tenant = self.account["id"] if self.account else None
        if not isinstance(workspace, str) or not 1 <= len(workspace) <= 100:
            raise ValueError("Invalid workspace")
        self.workspace = workspace
        if self.account:
            with store.connect(write=True) as db:
                db.execute(
                    "INSERT OR IGNORE INTO hosted_workspaces(tenant,id,name,created) "
                    "VALUES(?,'personal','Personal',?)",
                    (self.tenant, time.time()),
                )
            self.require_workspace()
        elif workspace != "personal":
            raise PermissionError("Sign in to use saved workspaces")

    def require_member(self):
        if not self.account:
            raise PermissionError("Sign in to create workspaces, projects or use knowledge")

    def require_workspace(self):
        self.require_member()
        with self.store.connect() as db:
            row = db.execute(
                "SELECT * FROM hosted_workspaces WHERE tenant=? AND id=?",
                (self.tenant, self.workspace),
            ).fetchone()
        if not row:
            raise LookupError("Workspace not found")
        return dict(row)

    def spaces(self):
        self.require_member()
        with self.store.connect() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT id,name,created AS created_at FROM hosted_workspaces "
                    "WHERE tenant=? ORDER BY created,id",
                    (self.tenant,),
                )
            ]

    def create_workspace(self, values):
        self.require_member()
        if set(values) != {"name"}:
            raise ValueError("Choose a workspace name")
        name = text(values["name"], "Workspace name", 100)
        identifier = "space-" + secrets.token_hex(12)
        with self.store.connect(write=True) as db:
            if (
                db.execute(
                    "SELECT count(*) FROM hosted_workspaces WHERE tenant=?",
                    (self.tenant,),
                ).fetchone()[0]
                >= 30
            ):
                raise ValueError("Workspace storage allowance reached")
            db.execute(
                "INSERT INTO hosted_workspaces(tenant,id,name,created) VALUES(?,?,?,?)",
                (self.tenant, identifier, name, time.time()),
            )
        return {"id": identifier, "name": name}

    def collections(self):
        self.require_member()
        with self.store.connect() as db:
            return [
                dict(row) | {"files": []}
                for row in db.execute(
                    "SELECT id,name,description,instructions,created AS created_at "
                    "FROM hosted_collections WHERE tenant=? AND workspace=? ORDER BY created,id",
                    (self.tenant, self.workspace),
                )
            ]

    def collection(self, identifier):
        self.require_member()
        if not isinstance(identifier, str) or not 1 <= len(identifier) <= 100:
            raise ValueError("Invalid project identifier")
        with self.store.connect() as db:
            row = db.execute(
                "SELECT * FROM hosted_collections WHERE tenant=? AND workspace=? AND id=?",
                (self.tenant, self.workspace, identifier),
            ).fetchone()
        if not row:
            raise LookupError("Project not found")
        return dict(row)

    def save_collection(self, values):
        self.require_member()
        if set(values) - {"id", "name", "description", "instructions"}:
            raise ValueError("Unsupported project field")
        identifier = values.get("id")
        if identifier:
            self.collection(identifier)
        else:
            identifier = "project-" + secrets.token_hex(12)
        name = text(values.get("name"), "Project name", 100)
        description = text(values.get("description", ""), "Description", 4000, optional=True)
        instructions = text(values.get("instructions", ""), "Instructions", 12000, optional=True)
        with self.store.connect(write=True) as db:
            if (
                not values.get("id")
                and db.execute(
                    "SELECT count(*) FROM hosted_collections WHERE tenant=? AND workspace=?",
                    (self.tenant, self.workspace),
                ).fetchone()[0]
                >= 100
            ):
                raise ValueError("Project storage allowance reached")
            db.execute(
                "INSERT INTO hosted_collections(id,tenant,workspace,name,description,"
                "instructions,created) VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                "name=excluded.name,description=excluded.description,instructions=excluded.instructions",
                (
                    identifier,
                    self.tenant,
                    self.workspace,
                    name,
                    description,
                    instructions,
                    time.time(),
                ),
            )
        return self.collection(identifier) | {"files": []}

    def conversation(self, identifier):
        if not isinstance(identifier, str) or not 1 <= len(identifier) <= 100:
            raise ValueError("Invalid conversation identifier")
        project = self.store.project(identifier, self.owner)
        if not project or project["workspace"] != self.workspace:
            raise LookupError("Conversation not found in this workspace")
        return project

    def assign(self, identifier, collection):
        self.require_member()
        self.conversation(identifier)
        if collection:
            self.collection(collection)
        with self.store.connect(write=True) as db:
            if db.execute(
                "SELECT 1 FROM jobs WHERE project=? AND status IN ('queued','running')",
                (identifier,),
            ).fetchone():
                raise ValueError("Wait for the current task before moving this conversation")
            db.execute(
                "UPDATE projects SET collection=? WHERE id=?", (collection or None, identifier)
            )

    def context(self, project):
        if not project.get("collection"):
            return None
        collection = self.collection(project["collection"])
        return {key: collection[key] for key in ("id", "name", "description", "instructions")}
