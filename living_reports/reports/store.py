"""Reports and their definition versions."""
import uuid
from typing import Any, Literal

from pydantic import BaseModel

from living_reports.metrics.runner import Fact, Result, _Sourced
from living_reports.reports.explore import CubeView
from living_reports.reports.payload import Tier
from living_reports.semantic.graph import FolioGraph
from living_reports.semantic.kinds import ReportPayload
from living_reports.spec.models import ReportSpec, View
from living_reports.store import Store


class StaleReport(Exception):
    """The report changed after this copy was read."""


class StoredReport(_Sourced):
    id: str = ""
    author: str
    tier: Tier
    view: View
    spec: ReportSpec
    facts: dict[str, Fact]
    queries: dict[str, Result]
    query_defs: dict[str, dict[str, Any]]
    version: int = 0
    cost: dict[str, Any] = {}
    contributors: list[str] = []
    tier_history: list[dict[str, str]] = []
    narration_refreshed: bool = False
    cube: CubeView | None = None
    cube_reason: str | None = None


class ThreadMessage(BaseModel):
    role: Literal["user", "assistant"]
    text: str


class Thread(BaseModel):
    id: str = ""
    report_id: str
    section_id: str
    fact_id: str | None = None
    messages: list[ThreadMessage] = []
    status: Literal["open", "closed"] = "open"
    decision: str | None = None


class ReportStore:
    def __init__(self, store: Store):
        self.store = store

    def save(self, report: StoredReport) -> StoredReport:
        """Write the report, and keep each version of its definition."""
        report.id = report.id or uuid.uuid4().hex[:12]
        with self.store.transaction():
            stored = self.store.row(
                "SELECT CAST(json_extract(payload, '$.version') AS INTEGER) FROM reports "
                "WHERE id = ?",
                [report.id],
            )
            if (stored[0] if stored else 0) != report.version:
                raise StaleReport(report.id)
            report.version += 1
            now = self.store.ts(self.store.now())
            self.store.execute(
                "INSERT OR REPLACE INTO reports VALUES (?, ?, ?)",
                [report.id, report.model_dump_json(), now],
            )
            self.store.execute(
                "INSERT INTO definitions VALUES (?, ?, ?, ?)",
                [report.id, report.version, report.spec.model_dump_json(), now],
            )
        return report

    def get(self, report_id: str) -> StoredReport | None:
        row = self.store.row("SELECT payload FROM reports WHERE id = ?", [report_id])
        return StoredReport.model_validate_json(row[0]) if row else None

    def list_reports(self) -> list[StoredReport]:
        rows = self.store.rows("SELECT payload FROM reports ORDER BY updated_at DESC, rowid DESC")
        return [StoredReport.model_validate_json(r[0]) for r in rows]

    def save_thread(self, thread: Thread) -> Thread:
        thread.id = thread.id or uuid.uuid4().hex[:12]
        self.store.execute(
            "INSERT OR REPLACE INTO threads VALUES (?, ?, ?)",
            [thread.id, thread.report_id, thread.model_dump_json()],
        )
        return thread

    def get_thread(self, thread_id: str) -> Thread | None:
        row = self.store.row("SELECT payload FROM threads WHERE id = ?", [thread_id])
        return Thread.model_validate_json(row[0]) if row else None

    def threads_for(self, report_id: str) -> list[Thread]:
        rows = self.store.rows(
            "SELECT payload FROM threads WHERE report_id = ? ORDER BY rowid", [report_id]
        )
        return [Thread.model_validate_json(r[0]) for r in rows]


def report_node(fg: FolioGraph, report: StoredReport) -> str:
    """The report's graph node id. The node is written when it does not exist."""
    node = f"report:{report.id}"
    if fg.slice().node(node) is None:
        payload = ReportPayload(title=report.spec.title, tier=report.tier, author=report.author)
        fg.put(node, "Report", payload, by=report.author)
    return node
