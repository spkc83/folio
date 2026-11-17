from living_reports.discovery.candidates import RelationshipCandidate, relationship_candidates
from living_reports.sources.base import TableInfo

from .conftest import _col


def test_declared_keys_come_first(catalog_with_fk):
    found = relationship_candidates(catalog_with_fk)
    assert found == [
        RelationshipCandidate(
            name="orders_dealers",
            from_dataset="orders",
            to_dataset="dealers",
            from_columns=["dealer_id"],
            to_columns=["dealer_id"],
            confidence=1.0,
            origin="declared",
        )
    ]


def test_name_match_on_a_primary_key(catalog):
    found = relationship_candidates(catalog)
    assert len(found) == 1
    c = found[0]
    assert (c.from_dataset, c.to_dataset, c.from_columns, c.to_columns) == (
        "orders", "dealers", ["dealer_id"], ["dealer_id"])
    assert c.origin == "name" and c.confidence == 0.6


def test_name_match_on_a_unique_column(catalog):
    dealers = catalog.table("main.dealers")
    dealers.primary_key = []
    found = relationship_candidates(catalog)
    assert [(c.from_dataset, c.to_dataset, c.origin) for c in found] == [
        ("orders", "dealers", "name")]


def test_name_match_needs_uniqueness(catalog):
    dealers = catalog.table("main.dealers")
    dealers.primary_key = []
    dealers.column("dealer_id").distinct_count = 5
    assert relationship_candidates(catalog) == []


def test_no_self_match(catalog):
    catalog.tables = [
        TableInfo(schema_name="main", name="t", row_count=3, primary_key=["k"],
                  columns=[_col("k", "string", 3)])
    ]
    assert relationship_candidates(catalog) == []


def test_an_id_column_matches_only_its_own_table_name(catalog):
    catalog.tables.append(
        TableInfo(schema_name="main", name="channels", row_count=4, primary_key=[],
                  columns=[_col("id", "string", 4)])
    )
    dealers = catalog.table("main.dealers")
    dealers.primary_key = []
    dealers.columns[0] = _col("id", "string", 7)
    found = relationship_candidates(catalog)
    # orders.dealer_id -> dealers.id; nothing goes to channels.id.
    assert [(c.to_dataset, c.from_columns, c.to_columns) for c in found] == [
        ("dealers", ["dealer_id"], ["id"])]


def test_a_wide_catalog_is_fast(catalog):
    import time

    catalog.tables = [
        TableInfo(schema_name="main", name=f"t{j}", row_count=10,
                  columns=[_col(f"c{i}", "string", 5) for i in range(1000)])
        for j in range(40)
    ]
    start = time.perf_counter()
    assert relationship_candidates(catalog) == []
    assert time.perf_counter() - start < 2
