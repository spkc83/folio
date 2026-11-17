import pytest

from living_reports.engine import OutOfScope
from living_reports.engine.paths import admissible, join_path, join_paths
from living_reports.semantic.model import Dataset, Relationship

from .conftest import make_field, replace_dataset


def _rel(name, a, b, from_cols, to_cols):
    return Relationship(name=name, from_dataset=a, to_dataset=b, from_columns=from_cols,
                        to_columns=to_cols, version=1)


def _with(model, datasets=(), relationships=()):
    return model.model_copy(update={
        "datasets": [*model.datasets, *datasets],
        "relationships": [*model.relationships, *relationships],
    })


def _chain(model):
    regions = Dataset(name="regions", source_id="duckdb_demo", table="main.regions",
                      primary_key=["region"], fields=[make_field("regions", "region")], version=1)
    countries = Dataset(name="countries", source_id="duckdb_demo", table="main.countries",
                        primary_key=["country"], fields=[make_field("countries", "country")],
                        version=1)
    return _with(model, [regions, countries], [
        _rel("dealer_region", "dealers", "regions", ["region"], ["region"]),
        _rel("region_country", "regions", "countries", ["country"], ["country"]),
    ])


def test_one_hop_path(model):
    path = join_path(model, "orders", "dealers")
    assert [j.relationship.name for j in path] == ["orders_dealer"]
    assert (path[0].from_dataset, path[0].to_dataset) == ("orders", "dealers")


def test_same_dataset_is_empty(model):
    assert join_path(model, "orders", "orders") == []


def test_two_hops_allowed_three_refused(model):
    m = _chain(model)
    assert [j.relationship.name for j in join_path(m, "orders", "regions")] == [
        "orders_dealer", "dealer_region"]
    with pytest.raises(OutOfScope, match="within 2 hops") as err:
        join_path(m, "orders", "countries")
    assert err.value.unknown == ["countries"]


def test_one_to_many_step_is_refused(model):
    m = _with(model, relationships=[_rel("dealer_orders", "dealers", "orders",
                                          ["dealer_id"], ["dealer_id"])])
    assert not admissible(m, m.relationships[-1])
    with pytest.raises(OutOfScope, match="dealer_orders joins orders on dealer_id, which is not its key"):
        join_path(m, "dealers", "orders")


def test_unique_key_is_admissible(model):
    m = _with(model, relationships=[_rel("dealer_orders", "dealers", "orders",
                                          ["dealer_id"], ["dealer_id"])])
    m = replace_dataset(m, "orders", unique_keys=[["dealer_id"]])
    assert [j.relationship.name for j in join_path(m, "dealers", "orders")] == ["dealer_orders"]


def test_cross_source_is_refused(model):
    m = replace_dataset(model, "dealers", source_id="crm")
    with pytest.raises(OutOfScope) as err:
        join_path(m, "orders", "dealers")
    assert err.value.reason == (
        "orders is in duckdb_demo, dealers is in crm; cross-source joins are not supported")


def test_direction_is_many_to_one_only(model):
    with pytest.raises(OutOfScope, match="No join path from dealers to orders"):
        join_path(model, "dealers", "orders")


def test_ties_break_by_relationship_name(model):
    m = _with(model, relationships=[_rel("a_orders_dealer", "orders", "dealers",
                                          ["dealer_id"], ["dealer_id"])])
    assert [j.relationship.name for j in join_path(m, "orders", "dealers")] == ["a_orders_dealer"]


def test_join_paths_one_per_target(model):
    paths = join_paths(model, "orders", ["dealers", "orders", "dealers"])
    assert set(paths) == {"dealers", "orders"}
    assert paths["orders"] == []
