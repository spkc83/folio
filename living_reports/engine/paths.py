"""Join paths: many-to-one steps over the model's relationships (spec 5.2.2).

A step from ``from_dataset`` to ``to_dataset`` is admissible only when its ``to`` columns
are the primary key or a declared unique key of ``to_dataset``. Then each row on the
``from`` side meets at most one row, so a join never multiplies a metric's rows.
"""

from collections.abc import Iterable
from dataclasses import dataclass

from living_reports.engine.errors import OutOfScope
from living_reports.semantic.model import Relationship, SemanticModel


@dataclass(frozen=True)
class Join:
    relationship: Relationship
    from_dataset: str
    to_dataset: str


def admissible(model: SemanticModel, rel: Relationship) -> bool:
    to = model.dataset(rel.to_dataset)
    return rel.to_columns == to.primary_key or rel.to_columns in to.unique_keys


def _blocked(model: SemanticModel, start: str, rel: Relationship) -> str | None:
    """Why the step ``rel`` cannot be taken from a path that starts at ``start``."""
    source = model.dataset(start).source_id
    to_source = model.dataset(rel.to_dataset).source_id
    if to_source != source:
        return (
            f"{start} is in {source}, {rel.to_dataset} is in {to_source}; "
            "cross-source joins are not supported"
        )
    if not admissible(model, rel):
        return f"{rel.name} joins {rel.to_dataset} on {', '.join(rel.to_columns)}, which is not its key"
    return None


def join_path(model: SemanticModel, start: str, target: str, max_hops: int = 2) -> list[Join]:
    """The shortest admissible path from ``start`` to ``target``. Ties break by name."""
    if start == target:
        return []
    rels = sorted(model.relationships, key=lambda r: r.name)
    frontier: list[tuple[str, list[Join]]] = [(start, [])]
    reasons: list[str] = []
    for _ in range(max_hops):
        found: list[list[Join]] = []
        nxt: list[tuple[str, list[Join]]] = []
        for at, path in frontier:
            seen = {start, *(j.to_dataset for j in path)}
            for rel in rels:
                if rel.from_dataset != at or rel.to_dataset in seen:
                    continue
                reason = _blocked(model, start, rel)
                if reason is not None:
                    if rel.to_dataset == target:
                        reasons.append(reason)
                    continue
                step = [*path, Join(rel, at, rel.to_dataset)]
                if rel.to_dataset == target:
                    found.append(step)
                else:
                    nxt.append((rel.to_dataset, step))
        if found:
            return min(found, key=lambda p: [j.relationship.name for j in p])
        frontier = nxt
    if reasons:
        raise OutOfScope(reasons[0], [target])
    raise OutOfScope(f"No join path from {start} to {target} within {max_hops} hops", [target])


def join_paths(
    model: SemanticModel, start: str, targets: Iterable[str], max_hops: int = 2
) -> dict[str, list[Join]]:
    return {t: join_path(model, start, t, max_hops) for t in dict.fromkeys(targets)}
