"""Tiers, checks, signatures, and publication."""
import json
from datetime import datetime

from pydantic import BaseModel

from living_reports.api.auth import role_of
from living_reports.checker.run import CheckContext, deterministic_pass, run_all
from living_reports.checker.steps import CheckResult, Finding
from living_reports.reports.certified import certify_edition
from living_reports.reports.editions import Edition, EditionStore
from living_reports.reports.payload import Tier
from living_reports.reports.store import ReportStore
from living_reports.store import Store

REQUIRED: dict[str, list[str]] = {
    "exploratory": [],
    "team": ["owner"],
    "business_critical": ["owner", "compliance"],
}


class Forbidden(Exception):
    pass


class Conflict(Exception):
    def __init__(self, detail: str, current_edition: int | None = None):
        super().__init__(detail)
        self.detail, self.current_edition = detail, current_edition


class Signature(BaseModel):
    role: str
    signer: str
    content_hash: str
    signed_at: datetime


class EditionState(BaseModel):
    number: int
    content_hash: str
    tier: Tier
    status: str
    author: str
    checks: list[CheckResult]
    signatures: list[Signature]
    needs: list[str]
    can_publish: bool
    newest: int


class Signoff:
    def __init__(self, store: Store, reports: ReportStore):
        self.store, self.reports, self.graph = store, reports, store.graph
        self.editions = EditionStore(store, reports)

    def _checks(self, edition: Edition) -> list[CheckResult]:
        row = self.store.row(
            "SELECT payload FROM check_runs WHERE report_id = ? AND number = ? "
            "AND content_hash = ?",
            [edition.report_id, edition.number, edition.content_hash],
        )
        if row is None:
            return []
        return [CheckResult.model_validate(c) for c in json.loads(row[0])]

    def _save_checks(self, edition: Edition, checks: list[CheckResult]) -> None:
        payload = "[" + ",".join(c.model_dump_json() for c in checks) + "]"
        self.store.execute(
            "INSERT OR REPLACE INTO check_runs VALUES (?, ?, ?, ?)",
            [edition.report_id, edition.number, edition.content_hash, payload],
        )

    def _signatures(self, report_id: str, number: int) -> list[Signature]:
        rows = self.store.rows(
            "SELECT role, signer, content_hash, signed_at FROM signatures "
            "WHERE report_id = ? AND number = ? ORDER BY signed_at",
            [report_id, number],
        )
        return [Signature(role=r[0], signer=r[1], content_hash=r[2], signed_at=r[3]) for r in rows]

    def state(self, edition: Edition) -> EditionState:
        report_id, number = edition.report_id, edition.number
        checks = self._checks(edition)
        signatures = [
            s for s in self._signatures(report_id, number) if s.content_hash == edition.content_hash
        ]
        signed = {s.role for s in signatures}
        needs = [r for r in REQUIRED[edition.tier] if r not in signed]
        wording_open = any(c.step == "wording" and c.status == "open" for c in checks)
        allowed = (
            edition.tier != "exploratory"
            and bool(checks)
            and deterministic_pass(checks)
            and not (edition.tier == "business_critical" and wording_open)
        )
        return EditionState(
            number=number,
            content_hash=edition.content_hash,
            tier=edition.tier,
            status=edition.status,
            author=edition.author,
            checks=checks,
            signatures=signatures,
            needs=needs,
            can_publish=allowed and not needs,
            newest=self.editions.newest(report_id).number,
        )

    def freeze_and_check(self, report_id: str) -> EditionState:
        edition = self.editions.freeze(report_id)
        if not self._checks(edition):
            checks = run_all(edition, CheckContext(graph=self.graph))
            self._save_checks(edition, checks)
        return self.state(edition)

    def _current(self, report_id: str, number: int) -> Edition:
        edition = self.editions.get(report_id, number)
        newest = self.editions.newest(report_id)
        if edition is None or newest.number != number:
            raise Conflict("A newer edition exists.", newest.number if newest else None)
        return edition

    def sign(self, report_id: str, number: int, content_hash: str, user: str) -> EditionState:
        role = role_of(user)
        edition = self._current(report_id, number)
        if user == edition.author or user in edition.contributors:
            raise Forbidden("The author of a report cannot sign it.")
        if role not in ("owner", "compliance"):
            raise Forbidden("Only an owner or a compliance user can sign.")
        if content_hash != edition.content_hash:
            raise Conflict("This page shows old content.", number)
        state = self.state(edition)
        if role in {s.role for s in state.signatures}:
            return state
        if role not in REQUIRED[edition.tier]:
            raise Conflict(f"This tier needs no {role} signature.", number)
        if not deterministic_pass(state.checks):
            raise Conflict("A failed check blocks signing.", number)
        if role == "compliance" and "owner" in state.needs:
            raise Conflict("The owner signs first.", number)
        self.store.execute(
            "INSERT INTO signatures VALUES (?, ?, ?, ?, ?, ?)",
            [report_id, number, role, user, content_hash, self.store.ts(self.store.now())],
        )
        return self.state(edition)

    def publish(self, report_id: str, number: int, user: str) -> EditionState:
        if role_of(user) != "owner":
            raise Forbidden("Only the owner publishes a report.")
        edition = self._current(report_id, number)
        state = self.state(edition)
        if not state.can_publish:
            raise Conflict("This edition does not meet the publication rules.", number)
        self._require_current_metrics(edition)
        with self.store.transaction():
            published = self.editions.mark_published(report_id, number)
            certify_edition(published, user, self.store)
        return self.state(published)

    def _require_current_metrics(self, edition: Edition) -> None:
        """A definition can change after the checks ran."""
        now = self.graph.slice()
        for fact in edition.facts.values():
            node = now.node(fact.metric) if fact.metric else None
            if node is not None and node.version != fact.metric_version:
                raise Conflict(f"The metric {fact.metric} changed after this edition.", None)

    def resolve_wording(self, report_id: str, number: int, note: str, user: str) -> EditionState:
        if role_of(user) != "compliance":
            raise Forbidden("Only a compliance user can resolve wording.")
        edition = self._current(report_id, number)
        checks = self._checks(edition)
        for check in checks:
            if check.step == "wording":
                check.status = "pass"
                check.findings.append(Finding(where=f"Reviewed by {user}", detail=note))
        self._save_checks(edition, checks)
        return self.state(edition)

    def set_tier(self, report_id: str, tier: Tier, user: str) -> None:
        if role_of(user) not in ("owner", "compliance"):
            raise Forbidden("Only an owner or a compliance user can set the tier.")
        report = self.reports.get(report_id)
        if report.tier == "exploratory" or tier == "exploratory":
            raise Conflict("An exploratory report needs certified metrics before promotion.")
        lowering = report.tier == "business_critical" and tier != report.tier
        if lowering and role_of(user) != "compliance":
            raise Forbidden("Only a compliance user can lower a business-critical report.")
        report.tier_history.append(
            {"from": report.tier, "to": tier, "by": user, "at": self.store.now().isoformat()}
        )
        report.tier = tier
        self.reports.save(report)

