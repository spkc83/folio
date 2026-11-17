"""Request-scoped dependencies. Every request shares the process's one store."""
from collections.abc import Iterator
from dataclasses import dataclass

from fastapi import Header, HTTPException

from living_reports.config import get_settings
from living_reports.reports.editions import EditionStore
from living_reports.reports.signoff import Signoff
from living_reports.reports.store import ReportStore
from living_reports.semantic.graph import FolioGraph
from living_reports.sources.registry import active_source
from living_reports.store import Store, get_store


@dataclass
class Stores:
    store: Store
    user: str

    @property
    def graph(self) -> FolioGraph:
        return self.store.graph

    @property
    def reports(self) -> ReportStore:
        return ReportStore(self.store)

    @property
    def editions(self) -> EditionStore:
        return EditionStore(self.store, self.reports)

    @property
    def signoff(self) -> Signoff:
        return Signoff(self.store, self.reports)

    def context(self, source_id: str | None = None):
        """The ask context on ``source_id``, or on the model's one source."""
        from living_reports.harness.builder import AskContext

        return AskContext(
            store=self.store,
            reports=self.reports,
            source_id=source_id or active_source(self.store),
            user=self.user,
        )


def stores(x_lr_user: str = Header(default="ana")) -> Iterator[Stores]:
    # This header is not authentication. It selects a seeded user in the demo only.
    if not get_settings().demo_mode:
        raise HTTPException(status_code=401, detail="This deployment has no identity provider.")
    yield Stores(get_store(), x_lr_user)
