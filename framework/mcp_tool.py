"""The `@tool` decorator, owned by the framework rather than by a vendor SDK.

tools.py and transfer.py declare their tools with `@tool(name, description, schema)`
and return MCP-shaped results. That was never specific to one harness, but importing
the decorator from claude_agent_sdk made it look as if it were. Declaring it here lets
the same tool definitions be served to anything: an out-of-process MCP server
(mcp_server.py), or an in-process host, without the tools themselves changing.

A schema is either the short form the campaigns use -- {"level": int} -- or a JSON
Schema object written out in full. Both are accepted, as before.
"""

_JSON_TYPES = {float: "number", int: "integer", str: "string", bool: "boolean"}

# Declaration order is the order tools are offered in, which is the order they were
# written in. Keyed by name so a reimport cannot register a tool twice.
REGISTRY = {}


def to_json_schema(schema):
    """The short {"name": type} form -> JSON Schema. A schema already written as JSON
    Schema is returned as it is."""
    if not schema:
        return {"type": "object", "properties": {}}
    if isinstance(schema, dict) and "type" in schema and "properties" in schema:
        return schema
    props = {name: {"type": _JSON_TYPES.get(typ, "string")}
             for name, typ in schema.items()}
    return {"type": "object", "properties": props,
            "required": sorted(props)}


class Tool:
    """One tool: what the model is told about it, and what to call."""

    def __init__(self, name, description, schema, handler):
        self.name = name
        self.description = description
        self.input_schema = to_json_schema(schema)
        self.handler = handler

    def spec(self):
        """The entry this tool contributes to an MCP tools/list reply."""
        return {"name": self.name, "description": self.description,
                "inputSchema": self.input_schema}


def tool(name, description, schema):
    """Register one tool and return the function unchanged, so it stays directly
    callable -- which is what the framework's own tests and callers rely on."""

    def decorate(fn):
        REGISTRY[name] = Tool(name, description, schema, fn)
        fn.tool_name = name
        return fn

    return decorate
