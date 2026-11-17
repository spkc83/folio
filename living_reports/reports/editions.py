"""Frozen editions. An edition never changes after it exists."""
import hashlib
import json
import threading
from datetime import datetime
from typing import Any, Literal

from sqlglot import exp

from living_reports.engine.compile import Compiled, compile
from living_reports.engine.query import stored_query
from living_reports.metrics.runner import Fact, Result, _Sourced
from living_reports.reports.explore import CubeView
from living_reports.reports.payload import ReportPayload, Tier, query_view
from living_reports.reports.store import ReportStore, StoredReport, report_node
from living_reports.semantic.kinds import EditionPayload
from living_reports.semantic.model import SemanticModel
from living_reports.sources import rows_of
from living_reports.sources.registry import source_for
from living_reports.spec.models import ReportSpec, View
from living_reports.store import Store

PLACEHOLDER = "<!--LR_PAYLOAD-->"
_freeze_lock = threading.Lock()
class Edition(_Sourced):
    report_id: str
    number: int
    spec: ReportSpec
    facts: dict[str, Fact]
    queries: dict[str, Result]
    query_defs: dict[str, dict[str, Any]]
    # The newest data time among the edition's queries (one source per edition).
    data_time: datetime | None = None
    known_at: datetime
    content_hash: str
    tier: Tier
    author: str
    view: View
    status: Literal["pending", "published"] = "pending"
    coverage: dict[str, Any] = {}
    contributors: list[str] = []
    narration_refreshed: bool = False
    cube: CubeView | None = None
    cube_reason: str | None = None
    graph_version: int = 0


def canonical_hash(payload: Any) -> str:
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode()).hexdigest()


def render_export(payload: dict, template: str) -> str:
    # "<" is escaped, so a value cannot close the script block.
    data = json.dumps(payload).replace("<", "\\u003c")
    block = f'<script type="application/json" id="lr-payload">{data}</script>'
    return template.replace(PLACEHOLDER, block)


def coverage_of(source_id: str, model: SemanticModel) -> dict[str, Any]:
    """Row count and date range of the model's timed dataset, for the completeness check."""
    timed = next((d for d in model.datasets if d.time_field), None)
    if timed is None:
        return {}
    source = source_for(source_id)
    column = exp.column(model.field(f"{timed.name}.{timed.time_field}").column)
    sql = exp.select(
        exp.Count(this=exp.Star()), exp.Min(this=column), exp.Max(this=column.copy())
    ).from_(exp.to_table(timed.table)).sql(dialect=source.dialect)
    rows, first, last = rows_of(source.run(sql, limit=1))[0]
    return {"rows": rows, "first": str(first), "last": str(last)}


def _data_time(report: StoredReport) -> datetime | None:
    times = [r.data_time for r in report.queries.values() if r.data_time is not None]
    return max(times) if times else None


def payload_of(edition: Edition) -> ReportPayload:
    return ReportPayload(
        id=edition.report_id,
        spec=edition.spec,
        facts=edition.facts,
        queries={k: query_view(k, v) for k, v in edition.queries.items()},
        edition=edition.number,
        state="published" if edition.status == "published" else "draft",
        tier=edition.tier,
        view=edition.view,
        cube=edition.cube,
        cube_reason=edition.cube_reason,
        query_defs=edition.query_defs,
    )


class EditionStore:
    def __init__(self, store: Store, reports: ReportStore):
        self.store, self.reports = store, reports

    def _insert(self, edition: Edition) -> Edition:
        """Plain INSERT: a number that exists stays as it is, so an edition cannot be replaced."""
        self.store.execute(
            "INSERT INTO editions VALUES (?, ?, ?, ?)",
            [edition.report_id, edition.number, edition.status, edition.model_dump_json()],
        )
        return edition

    def draft_hash(self, report: StoredReport) -> str:
        return canonical_hash(
            {
                "spec": report.spec.model_dump(mode="json"),
                "facts": {k: v.model_dump(mode="json") for k, v in report.facts.items()},
                "queries": {
                    k: v.model_dump(exclude={"from_cache"}) for k, v in report.queries.items()
                },
                "source_id": report.source_id,
                "tier": report.tier,
            }
        )

    def freeze(self, report_id: str) -> Edition:
        """Freeze the draft. The same content gives the same edition."""
        report = self.reports.get(report_id)
        digest = self.draft_hash(report)
        with _freeze_lock:
            latest = self.newest(report_id)
            if latest and latest.content_hash == digest:
                return latest
            edition = self._edition(report, digest, latest)
            with self.store.transaction():
                self._insert(edition)
                self._put_node(edition)
            return edition

    def _put_node(self, edition: Edition) -> None:
        """The edition's graph node, its report, and the metrics its facts cite."""
        fg = self.store.graph
        node = f"edition:{edition.report_id}:{edition.number}"
        payload = EditionPayload(
            report_id=edition.report_id,
            number=edition.number,
            known_at=edition.known_at,
            graph_version=edition.graph_version,
            content_hash=edition.content_hash,
        )
        report_node(fg, self.reports.get(edition.report_id))
        fg.put(node, "Edition", payload, by=edition.author)
        for metric in sorted({f.metric for f in edition.facts.values() if f.metric}):
            fg.link("cites", node, metric, by=edition.author)

    def _edition(self, report: StoredReport, digest: str, latest: Edition | None) -> Edition:
        known_at = self.store.now()
        return Edition(
            report_id=report.id,
            number=(latest.number if latest else 0) + 1,
            spec=report.spec,
            facts=report.facts,
            queries=report.queries,
            query_defs=report.query_defs,
            source_id=report.source_id,
            data_time=_data_time(report),
            known_at=known_at,
            graph_version=self.store.graph.version(known_at),
            content_hash=digest,
            tier=report.tier,
            author=report.author,
            view=report.view,
            coverage=coverage_of(report.source_id, self.store.graph.model(known_at, known_at)),
            contributors=report.contributors,
            narration_refreshed=report.narration_refreshed,
            cube=report.cube,
            cube_reason=report.cube_reason,
        )

    def get(self, report_id: str, number: int) -> Edition | None:
        row = self.store.row(
            "SELECT payload FROM editions WHERE report_id = ? AND number = ?", [report_id, number]
        )
        return Edition.model_validate_json(row[0]) if row else None

    def newest(self, report_id: str) -> Edition | None:
        row = self.store.row(
            "SELECT payload FROM editions WHERE report_id = ? ORDER BY number DESC LIMIT 1",
            [report_id],
        )
        return Edition.model_validate_json(row[0]) if row else None

    def latest(self, report_id: str) -> Edition | None:
        row = self.store.row(
            "SELECT payload FROM editions WHERE report_id = ? AND status = 'published' "
            "ORDER BY number DESC LIMIT 1",
            [report_id],
        )
        return Edition.model_validate_json(row[0]) if row else None

    def mark_published(self, report_id: str, number: int) -> Edition:
        edition = self.get(report_id, number)
        edition.status = "published"
        self.store.execute(
            "UPDATE editions SET status = ?, payload = ? WHERE report_id = ? AND number = ?",
            [edition.status, edition.model_dump_json(), report_id, number],
        )
        return edition


def reproduce(edition: Edition, store: Store) -> dict[str, Compiled]:
    """Compile the edition's queries again from the model as it was at ``known_at``."""
    model = store.graph.model(valid_at=edition.known_at, known_at=edition.known_at)
    return {
        ref: compile(stored_query(definition, model), model)
        for ref, definition in edition.query_defs.items()
    }
