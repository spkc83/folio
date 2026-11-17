"""Tools for generated code, defined as MCP schemas and declared as TypeScript."""
from typing import Any

from mcp.types import Tool

from living_reports.engine import Query
from living_reports.semantic.model import OPS

TOOLS: list[Tool] = [
    Tool(
        name="metrics_query",
        description="Run a certified metric query. Returns at most 20 rows and traced facts.",
        # Filter groups nest, so the schema keeps its $defs; the declarations name them.
        inputSchema=Query.model_json_schema(),
    ),
    Tool(
        name="graph_lookup",
        description="Read one metric or dimension from the vocabulary, by its id.",
        inputSchema={
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"],
        },
    ),
    Tool(
        name="report_add_section",
        description="Add a section of words. Use {fact:<id>} tokens for every value.",
        inputSchema={
            "type": "object",
            "properties": {
                "heading": {"type": "string"},
                "paragraphs": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["heading", "paragraphs"],
        },
    ),
]


_REF_TYPES = {
    "FilterSpec": "Filter",
    "FilterGroup": "FilterGroup",
    "TimeRange": "TimeRange",
    "Having": "Having",
}
_OPS = " | ".join(f'"{o}"' for o in OPS)


def _ts_type(schema: dict[str, Any]) -> str:
    if "anyOf" in schema:
        return " | ".join(_ts_type(s) for s in schema["anyOf"])
    if "$ref" in schema:
        return _REF_TYPES.get(schema["$ref"].split("/")[-1], "unknown")
    if "prefixItems" in schema:
        return "[" + ", ".join(_ts_type(s) for s in schema["prefixItems"]) + "]"
    if "enum" in schema:
        return " | ".join(f'"{v}"' for v in schema["enum"])
    if "const" in schema:
        return f'"{schema["const"]}"'
    kind = schema.get("type")
    if kind == "array":
        return f"({_ts_type(schema.get('items', {}))})[]"
    if kind == "object":
        return _ts_object(schema)
    return {
        "string": "string",
        "integer": "number",
        "number": "number",
        "boolean": "boolean",
        "null": "null",
    }.get(kind, "unknown")


def _ts_object(schema: dict[str, Any]) -> str:
    required = set(schema.get("required", []))
    parts = [
        f"{name}{'' if name in required else '?'}: {_ts_type(prop)}"
        for name, prop in schema.get("properties", {}).items()
    ]
    return "{ " + "; ".join(parts) + " }"


def declarations(tools: list[Tool]) -> str:
    """TypeScript declarations that replace long tool schemas in the prompt."""
    lines = [
        f"type Filter = {{ member: string; op: {_OPS}; values: (string | number | boolean)[] }};",
        "type FilterGroup = { and?: (Filter | FilterGroup)[]; or?: (Filter | FilterGroup)[] };",
        f"type Having = {{ metric: string; op: {_OPS}; values: (string | number | boolean)[] }};",
        "type TimeRange = { field?: string; start?: string; end?: string; "
        'grain?: "day" | "week" | "month" | "quarter" | "year" };',
        "type QueryResult = { columns: string[]; rows: unknown[][]; row_count: number; "
        "facts: { id: string; label: string; formatted: string }[] };",
        "declare const tools: {",
    ]
    for tool in tools:
        lines.append(f"  /** {tool.description} */")
        lines.append(f"  {tool.name}(args: {_ts_object(tool.inputSchema)}): Promise<any>;")
    lines.append("};")
    return "\n".join(lines)
