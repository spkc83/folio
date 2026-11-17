"""Search over report titles and fact labels, in one SQL statement."""
from pydantic import BaseModel

from living_reports.store import Store

MAX_LABELS = 3

# Arm 0 matches titles, arm 1 matches fact labels. ``f.id`` keeps the facts in their order.
_SQL = r"""
SELECT id, json_extract(payload, '$.spec.title') AS title, json_extract(payload, '$.tier') AS tier,
       NULL AS label, updated_at, 0 AS arm, 0 AS ord
FROM reports
WHERE lower(json_extract(payload, '$.spec.title')) LIKE ? ESCAPE '\'
UNION ALL
SELECT r.id, json_extract(r.payload, '$.spec.title'), json_extract(r.payload, '$.tier'),
       json_extract(f.value, '$.label'), r.updated_at, 1, f.id
FROM reports r, json_each(json_extract(r.payload, '$.facts')) f
WHERE lower(json_extract(f.value, '$.label')) LIKE ? ESCAPE '\'
ORDER BY updated_at DESC, id, arm, ord
"""


class SearchHit(BaseModel):
    report_id: str
    title: str
    tier: str
    labels: list[str]


def escape_like(term: str) -> str:
    """Lowercase, and make ``\\``, ``%``, and ``_`` literal under ``ESCAPE '\\'``."""
    return term.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def search_reports(store: Store, term: str, limit: int = 20) -> list[SearchHit]:
    term = term.strip()
    if not term:
        return []
    pattern = f"%{escape_like(term)}%"
    hits: dict[str, SearchHit] = {}
    for report_id, title, tier, label, *_ in store.rows(_SQL, [pattern, pattern]):
        hit = hits.get(report_id)
        if hit is None:
            if len(hits) == limit:
                continue
            hit = hits[report_id] = SearchHit(report_id=report_id, title=title, tier=tier, labels=[])
        if label is not None and label not in hit.labels and len(hit.labels) < MAX_LABELS:
            hit.labels.append(label)
    return list(hits.values())
