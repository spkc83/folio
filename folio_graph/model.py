"""The graph's value types. Nothing here knows about any domain."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

Status = Literal["candidate", "confirmed", "retired"]
Cardinality = Literal["one", "many"]
ANY = "*"


class GraphError(Exception):
    """A rule of the graph was broken. The message is a sentence a person can read."""


@dataclass(frozen=True)
class Node:
    id: str
    kind: str
    payload: dict
    status: Status
    version: int
    valid_from: datetime
    valid_to: datetime | None
    recorded_from: datetime
    recorded_to: datetime | None


@dataclass(frozen=True)
class Edge:
    id: str
    kind: str
    from_id: str
    to_id: str
    role: str | None
    payload: dict
    status: Status
    version: int
    valid_from: datetime
    valid_to: datetime | None
    recorded_from: datetime
    recorded_to: datetime | None


@dataclass(frozen=True)
class EdgeRule:
    """``from_kind`` and ``to_kind`` may be ``"*"`` for any kind.

    ``cardinality="one"`` means a node has at most one live edge of this kind going out.
    """

    edge_kind: str
    from_kind: str
    to_kind: str
    cardinality: Cardinality = "many"


@dataclass(frozen=True)
class KindSpec:
    name: str
    schema: type[BaseModel]
    edges: tuple[EdgeRule, ...] = field(default_factory=tuple)


class NodeChange(BaseModel):
    id: str
    kind: str
    payload: dict


class EdgeChange(BaseModel):
    kind: str
    from_id: str
    to_id: str
    role: str | None = None
    payload: dict = {}

    @property
    def id(self) -> str:
        return f"{self.from_id}|{self.kind}|{self.to_id}|{self.role or ''}"


class Proposal(BaseModel):
    id: str
    change: NodeChange | EdgeChange
    reason: str
    proposed_by: str
    created_at: datetime
    status: Literal["pending", "confirmed", "rejected"] = "pending"
    decided_by: str | None = None
    decided_at: datetime | None = None
    note: str | None = None


@dataclass(frozen=True)
class Diff:
    """Entity ids, sorted. ``changed`` holds ``(id, version_a, version_b)``."""

    added: list[str]
    removed: list[str]
    changed: list[tuple[str, int, int]]
