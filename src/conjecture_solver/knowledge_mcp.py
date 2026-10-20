"""Read-only MCP view of one explicitly selected local knowledge workspace."""

import asyncio

DEFINITIONS = (
    {
        "name": "knowledge_search",
        "description": (
            "Search connected research files, completed conversations and study reports. "
            "Results are context, not accepted evidence. Read matching sources before citing them."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "kind": {"type": "string", "default": "all"},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "knowledge_read",
        "description": "Read an exact indexed source section with its revision and citation URL.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "revision": {"type": "string"},
                "chunk": {"type": "integer", "minimum": 0, "default": 0},
            },
            "required": ["id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "knowledge_status",
        "description": "List connected sources and their indexing freshness.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
)


def create_server(knowledge, conversation=None):
    from mcp import types

    try:
        from mcp.server.lowlevel import Server
    except ImportError:
        from mcp.server import Server
    from .mcp_server import _model, _sdk_result, _sdk_tool

    async def listing(*_args, **_kwargs):
        tools = [_sdk_tool(types, item) for item in DEFINITIONS]
        return (
            _model(types.ListToolsResult, tools=tools)
            if hasattr(types, "ListToolsResult")
            else tools
        )

    async def call(*args, **kwargs):
        request = next(
            (
                item
                for item in (*args, *kwargs.values())
                if hasattr(item, "name") and hasattr(item, "arguments")
            ),
            None,
        )
        name = request.name if request else args[0]
        arguments = (request.arguments if request else args[1]) or {}
        try:
            if name == "knowledge_search":
                result = await asyncio.to_thread(
                    knowledge.search,
                    arguments["query"],
                    kind=arguments.get("kind", "all"),
                    conversation=conversation,
                )
            elif name == "knowledge_read":
                result = await asyncio.to_thread(
                    knowledge.read,
                    arguments["id"],
                    revision=arguments.get("revision"),
                    chunk=arguments.get("chunk", 0),
                    conversation=conversation,
                )
                if conversation:
                    knowledge.receipt(conversation, result)
            elif name == "knowledge_status":
                result = knowledge.status()
                allowed = knowledge.allowed(conversation=conversation)
                result["sources"] = [s for s in result["sources"] if s["id"] in allowed]
            else:
                raise ValueError("Unknown knowledge tool")
            return _sdk_result(types, result)
        except (ValueError, OSError, KeyError, TypeError) as error:
            return _sdk_result(types, {"error": str(error)}, error=True)

    try:
        return Server(
            "simjecture-knowledge", version="1.0.0", on_list_tools=listing, on_call_tool=call
        )
    except TypeError:
        server = Server("simjecture-knowledge")
        server.list_tools()(listing)
        server.call_tool()(call)
        return server


def run(knowledge, conversation=None):
    from .mcp_server import run_stdio

    asyncio.run(run_stdio(server=create_server(knowledge, conversation)))
    return 0
