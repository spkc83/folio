from datetime import datetime

import pytest
from pydantic import BaseModel

from folio_graph import EdgeRule, KindSpec


class Clock:
    """A clock that tests move by hand."""

    def __init__(self, start: datetime):
        self.now = start

    def set(self, when: datetime) -> None:
        self.now = when

    def __call__(self) -> datetime:
        return self.now


class Metric(BaseModel):
    label: str
    definition: str
    expr: dict


class Decision(BaseModel):
    text: str
    approved_by: str


class Thread(BaseModel):
    title: str


class Dataset(BaseModel):
    name: str
    primary_key: list[str]


class Report(BaseModel):
    title: str


KINDS = [
    KindSpec("Metric", Metric, (EdgeRule("derived_from", "Metric", "Decision"),)),
    KindSpec("Decision", Decision, (EdgeRule("decided_in", "Decision", "Thread", "one"),)),
    KindSpec(
        "Thread",
        Thread,
        (
            EdgeRule("uses", "Thread", "Metric"),
            EdgeRule("discussed_in", "Thread", "Report"),
        ),
    ),
    KindSpec("Dataset", Dataset, (EdgeRule("joins_to", "Dataset", "Dataset"),)),
    KindSpec("Report", Report, (EdgeRule("uses", "Report", "Metric"),)),
]


@pytest.fixture
def clock() -> Clock:
    return Clock(datetime(2024, 1, 1))


@pytest.fixture
def kinds() -> list[KindSpec]:
    return KINDS
