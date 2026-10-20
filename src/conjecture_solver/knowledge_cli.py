"""JSON knowledge tools usable by any local agent, independently of the browser."""

import json
from pathlib import Path

from .workspace_projects import WorkspaceDirectory


def configure(parser):
    parser.add_argument("--runs-root", type=Path, default=Path("artifacts"))
    parser.add_argument("--workspace", default="personal")
    parser.add_argument("--conversation", help="Apply this conversation's source selection")
    actions = parser.add_subparsers(dest="action", required=True)
    actions.add_parser("status")
    actions.add_parser("sync")
    add = actions.add_parser("add")
    add.add_argument("path")
    add.add_argument("--name")
    remove = actions.add_parser("remove")
    remove.add_argument("id")
    search = actions.add_parser("search")
    search.add_argument("query")
    search.add_argument("--kind", default="all")
    search.add_argument("--source", action="append")
    read = actions.add_parser("read")
    read.add_argument("id")
    read.add_argument("--revision")
    read.add_argument("--chunk", type=int, default=0)
    actions.add_parser("mcp", help="Serve read-only knowledge tools over MCP stdio")
    parser.set_defaults(handler=run)


def run(args):
    try:
        knowledge = WorkspaceDirectory(args.runs_root).open(args.workspace).knowledge
        if args.action == "mcp":
            from .knowledge_mcp import run as run_mcp

            return run_mcp(knowledge, args.conversation)
        if args.action == "status":
            result = knowledge.status()
        elif args.action == "add":
            result = knowledge.add({"path": args.path, "name": args.name})
            # CLI processes must finish their requested ingestion before exiting.
            knowledge.sync(force=True)
            if knowledge._thread:
                knowledge._thread.join()
        elif args.action == "remove":
            result = knowledge.configure({"id": args.id, "remove": True})
        elif args.action == "sync":
            knowledge.sync(force=True)
            result = knowledge.status()
        elif args.action == "search":
            result = knowledge.search(
                args.query, kind=args.kind, sources=args.source, conversation=args.conversation
            )
        else:
            result = knowledge.read(
                args.id, revision=args.revision, chunk=args.chunk, conversation=args.conversation
            )
            if args.conversation:
                knowledge.receipt(args.conversation, result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, TypeError, KeyError) as error:
        print(json.dumps({"error": str(error)}))
        return 2
