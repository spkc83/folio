"""The host side of the tool channel. Credentials and data connections stay here."""
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from living_reports.engine import OutOfScope, Query, QueryRejected
from living_reports.metrics.runner import Fact, Result, run_query, to_facts
from living_reports.store import Store

MAX_ROWS = 20


class _Section(BaseModel):
    heading: str
    paragraphs: list[str]


@dataclass
class SandboxContext:
    store: Store
    source_id: str
    queries: list[tuple[Query, Result]] = field(default_factory=list)
    facts: list[Fact] = field(default_factory=list)
    sections: list[dict[str, Any]] = field(default_factory=list)


def _metrics_query(args: dict, ctx: SandboxContext) -> dict[str, Any]:
    query = Query.model_validate(args)
    result = run_query(query, ctx.source_id, ctx.store)
    ref = f"q{len(ctx.queries) + 1}"
    facts = to_facts(result, ref)
    ctx.queries.append((query, result))
    ctx.facts += facts
    return {
        "columns": result.columns,
        "rows": result.rows[:MAX_ROWS],
        "row_count": result.row_count,
        "facts": [
            {"id": f.id, "label": f.label, "formatted": f.formatted} for f in facts[:MAX_ROWS]
        ],
    }


def _graph_lookup(args: dict, ctx: SandboxContext) -> dict[str, Any]:
    """One term from the vocabulary. Raw graph nodes never reach generated code."""
    wanted = str(args.get("id"))
    vocabulary = ctx.store.graph.vocabulary()
    for kind, terms in (("metric", vocabulary.metrics), ("dimension", vocabulary.dimensions)):
        for term in terms:
            if term.id == wanted:
                return {"kind": kind, **term.model_dump(mode="json")}
    raise ValueError("No such metric or dimension.")


def _add_section(args: dict, ctx: SandboxContext) -> dict[str, bool]:
    ctx.sections.append(_Section.model_validate(args).model_dump())
    return {"ok": True}


HANDLERS = {
    "metrics_query": _metrics_query,
    "graph_lookup": _graph_lookup,
    "report_add_section": _add_section,
}


def dispatch(tool: str, args: Any, ctx: SandboxContext) -> dict[str, Any]:
    """Run one tool. Any failure goes back to the generated code as an error message."""
    handler = HANDLERS.get(tool)
    if handler is None:
        return {"error": f"Unknown tool: {tool}"}
    try:
        return {"result": handler(args, ctx)}
    except ValidationError as error:
        return {"error": f"Invalid arguments: {error.error_count()} problem(s)"}
    except (OutOfScope, QueryRejected) as error:
        return {"error": error.reason}
    except ValueError as error:
        return {"error": str(error)}
    except Exception:  # generated code controls these arguments, so no input may crash the host
        return {"error": f"The tool {tool} could not run with these arguments."}
