import json

import pytest
import yaml

from living_reports.discovery.preview import model_from_changes
from living_reports.engine import Query
from living_reports.engine.compile import compile
from living_reports.engine.expressions import parse_expression
from living_reports.semantic.kinds import (
    DatasetPayload,
    DimensionSpec,
    FieldPayload,
    MetricBindingPayload,
    RelationshipPayload,
    SourcePayload,
)
from living_reports.semantic.model import Metric
from living_reports.semantic.ossie import (
    OSSIE_VERSION,
    document_to_changes,
    export_model,
    parse_document,
)

from ..discovery_tests.conftest import make_catalog
from ..engine_tests.conftest import add_metric, build_model

SPEC_QUERY = Query.model_validate({
    "metrics": ["fraud_loss"], "dimensions": ["orders.sales_channel"],
    "filters": [{"member": "dealers.region", "op": "eq", "values": ["East"]}],
    "time": {"field": "orders.order_created_date", "start": "2025-01-01", "end": "2026-01-01",
             "grain": None},
    "order": [["fraud_loss", "desc"]], "limit": None,
})
FIELD_KEYS = {"name", "expression", "dimension", "label", "description", "datatype",
              "ai_context", "custom_extensions"}
METRIC_KEYS = {"name", "expression", "description", "datatype", "ai_context", "custom_extensions"}
TOP_KEYS = {"version", "name", "description", "datasets", "relationships", "metrics",
            "ai_context", "custom_extensions"}


@pytest.fixture
def model():
    m = build_model()
    loss = m.metric("fraud_loss").model_copy(update={
        "label": "Fraud loss", "definition": "Total loss on orders confirmed as fraud.",
        "synonyms": ["loss"]})
    return add_metric(m, loss)


@pytest.fixture
def m_expr(model):
    expr = parse_expression("sum(orders.loss_amount) filter (where orders.fraud_type <> "
                            "'friendly_fraud')", "orders", model)
    return add_metric(model, Metric(id="net_expr", label="Net", format="currency",
                                    expression=expr, certified=False, version=1))


def _doc(model):
    return yaml.safe_load(export_model(model))


def _by_name(items, name):
    return next(x for x in items if x["name"] == name)


def test_export_shape(model):
    doc = _doc(model)
    assert doc["version"] == OSSIE_VERSION == "0.2.0.dev0"
    assert doc["name"] == "duckdb_demo"
    assert [d["name"] for d in doc["datasets"]] == ["orders", "dealers"]
    orders = doc["datasets"][0]
    assert orders["source"] == "main.orders" and orders["primary_key"] == ["order_id"]
    fields = orders["fields"]
    assert _by_name(fields, "sales_channel") == {
        "name": "sales_channel", "expression": {"sql": "sales_channel"}, "datatype": "String",
        "label": "Sales channel", "dimension": {"is_time": False}}
    assert _by_name(fields, "order_created_date")["dimension"] == {"is_time": True}
    assert "dimension" not in _by_name(fields, "loss_amount")
    assert _by_name(fields, "loss_k")["expression"] == {
        "dialects": [{"dialect": "DUCKDB", "expression": "loss_amount / 1000"}]}
    assert doc["relationships"] == [{"name": "orders_dealer", "from": "orders", "to": "dealers",
                                     "from_columns": ["dealer_id"], "to_columns": ["dealer_id"]}]
    loss = _by_name(doc["metrics"], "fraud_loss")
    assert loss["expression"]["dialects"][0]["expression"] == "SUM(orders.loss_amount)"
    assert loss["description"] == "Total loss on orders confirmed as fraud."
    assert loss["ai_context"] == {"synonyms": ["loss"]}
    assert json.loads(loss["custom_extensions"][0]["data"]) == {
        "label": "Fraud loss", "format": "currency", "certified": True,
        "measure": {"agg": "sum", "field": "orders.loss_amount", "dataset": None, "filter": None}}
    assert loss["custom_extensions"][0]["vendor_name"] == "folio"
    net = _by_name(doc["metrics"], "net_fraud_loss")["expression"]["dialects"][0]["expression"]
    assert "FILTER(WHERE orders.fraud_type <> 'friendly_fraud')" in net
    ratio = _by_name(doc["metrics"], "loss_per_order")
    assert "/ NULLIF(COUNT(*), 0)" in ratio["expression"]["dialects"][0]["expression"]
    assert "ratio" in json.loads(ratio["custom_extensions"][0]["data"])


def test_export_has_no_folio_only_keys_at_the_top(m_expr):
    doc = _doc(m_expr)
    assert set(doc) <= TOP_KEYS
    for d in doc["datasets"]:
        for f in d["fields"]:
            assert set(f) <= FIELD_KEYS
    for m in doc["metrics"]:
        assert set(m) <= METRIC_KEYS


def _expected(model):
    """The payload of every node of ``model``, by id."""
    out = {model.source: SourcePayload(name=model.source).model_dump(mode="json")}
    for d in model.datasets:
        out[d.name] = DatasetPayload(
            **d.model_dump(exclude={"fields", "version"})).model_dump(mode="json")
        for f in d.fields:
            out[f.id] = FieldPayload(**f.model_dump(exclude={"id", "version"})).model_dump(mode="json")
    for r in model.relationships:
        out[r.name] = RelationshipPayload(**r.model_dump(exclude={"version"})).model_dump(mode="json")
    for m in model.metrics:
        out[m.id] = MetricBindingPayload(
            name=m.id, **m.model_dump(exclude={"id", "version"})).model_dump(mode="json")
    return out


def test_round_trip_reproduces_the_payloads(model):
    changes, dropped = document_to_changes(parse_document(export_model(model)),
                                           SourcePayload(name="duckdb_demo"))
    assert dropped == []
    got = {c.id: c.payload for c in changes}
    for node_id, payload in _expected(model).items():
        assert got[node_id] == payload, node_id
    assert set(got) == set(_expected(model))
    assert [c.kind for c in changes][:3] == ["Source", "Dataset", "Dataset"]
    rebuilt = model_from_changes(changes)
    assert compile(SPEC_QUERY, rebuilt).sql == compile(SPEC_QUERY, model).sql


def test_round_trip_of_an_uncertified_expression(m_expr):
    changes, dropped = document_to_changes(parse_document(export_model(m_expr)),
                                           SourcePayload(name="duckdb_demo"))
    assert dropped == []
    net = next(c for c in changes if c.id == "net_expr").payload
    assert net["certified"] is False
    assert net["expression"] == m_expr.metric("net_expr").expression.model_dump(mode="json")


def _document(metrics=(), fields=None, datasets_extra=None, version="0.2.0.dev0", **top):
    orders = {"name": "orders", "source": "main.orders", "primary_key": ["order_id"],
              "fields": fields or [
                  {"name": "order_id", "expression": {"sql": "order_id"}, "datatype": "String"},
                  {"name": "loss_amount", "expression": {"sql": "loss_amount"},
                   "datatype": "Decimal"},
                  {"name": "fraud_type", "expression": {"sql": "fraud_type"},
                   "datatype": "String", "dimension": {}}]}
    orders.update(datasets_extra or {})
    return yaml.safe_dump({"version": version, "name": "imported", "datasets": [orders],
                           "metrics": list(metrics), **top})


def _changes(text, catalog=None):
    return document_to_changes(parse_document(text), SourcePayload(name="test"), catalog)


def test_sql_only_metric_imports_uncertified():
    changes, dropped = _changes(_document([{
        "name": "net_loss", "description": "Net loss",
        "expression": {"sql": "sum(orders.loss_amount) filter (where orders.fraud_type <> "
                              "'friendly_fraud')"}}]))
    assert dropped == []
    p = next(c for c in changes if c.id == "net_loss").payload
    assert p["expression"]["fields"] == ["orders.fraud_type", "orders.loss_amount"]
    assert p["certified"] is False and p["format"] == "number"
    assert p["definition"] == "Net loss" and p["label"] == "Net loss"


def test_an_extension_cannot_certify_an_expression():
    data = json.dumps({"label": "Net", "format": "currency", "certified": True,
                       "expression": {"sql": "sum(orders.loss_amount)", "dialect": "duckdb",
                                      "dataset": "orders", "fields": ["orders.loss_amount"],
                                      "ast": {"class": "Sum"}}})
    changes, dropped = _changes(_document([{
        "name": "net", "expression": {"sql": "sum(orders.loss_amount)"},
        "custom_extensions": [{"vendor_name": "folio", "data": data}]}]))
    assert dropped == []
    p = next(c for c in changes if c.id == "net").payload
    assert p["certified"] is False and p["label"] == "Net" and p["format"] == "currency"
    assert p["expression"]["ast"] != {"class": "Sum"}


def test_sql_metric_that_breaks_the_expression_rules_is_dropped():
    changes, dropped = _changes(_document([
        {"name": "bad", "expression": {"sql": "sum(orders.loss_amount) / count(dealers.dealer_id)"}},
        {"name": "sub", "expression": {"sql": "sum((select 1))"}},
        {"name": "reader", "expression": {"sql": "sum(read_csv('/etc/passwd'))"}},
        {"name": "good", "expression": {"sql": "sum(orders.loss_amount)"}},
    ]))
    reasons = {d.name: d.reason for d in dropped}
    assert set(reasons) == {"bad", "sub", "reader"}
    assert all(d.kind == "MetricBinding" for d in dropped)
    assert "aggregate" in reasons["bad"] or "not a field of" in reasons["bad"]
    assert any(c.id == "good" for c in changes)


def test_dataset_scoped_metric_is_lifted():
    changes, dropped = _changes(_document(datasets_extra={"metrics": [
        {"name": "loss", "expression": {"sql": "sum(loss_amount)"}}]}))
    assert dropped == []
    p = next(c for c in changes if c.id == "loss").payload
    assert p["expression"]["fields"] == ["orders.loss_amount"]
    assert p["expression"]["dataset"] == "orders"


def test_unsupported_datatype_and_bad_version():
    fields = [{"name": "order_id", "expression": {"sql": "order_id"}, "datatype": "String"},
              {"name": "blob", "expression": {"sql": "blob"}, "datatype": "Opaque"}]
    changes, dropped = _changes(_document(fields=fields))
    assert [(d.name, d.reason) for d in dropped] == [("orders.blob", "unsupported datatype Opaque")]
    with pytest.raises(ValueError, match="0.2"):
        parse_document(_document(version="1.0"))


def test_missing_dimension_key_follows_the_ossie_default():
    fields = [{"name": "ts", "expression": {"sql": "ts"}, "datatype": "DateTime"},
              {"name": "s", "expression": {"sql": "s"}, "datatype": "String"}]
    changes, _ = _changes(_document(fields=fields))
    by_id = {c.id: c.payload for c in changes}
    assert by_id["orders.ts"]["dimension"] == DimensionSpec(is_time=True).model_dump()
    assert by_id["orders.s"]["dimension"] is None


def test_import_checks_the_catalog():
    changes, dropped = _changes(_document(datasets_extra={"source": "main.ghost"}),
                                make_catalog())
    assert ("orders", "main.ghost is not a table of test") in [(d.name, d.reason) for d in dropped]
    assert not any(c.kind in ("Dataset", "Field") for c in changes)


def test_import_checks_columns_against_the_catalog():
    fields = [{"name": "nope", "expression": {"sql": "nope"}, "datatype": "String"},
              {"name": "loss_amount", "expression": {"sql": "loss_amount"}, "datatype": "Decimal"}]
    changes, dropped = _changes(_document(fields=fields), make_catalog())
    assert [(d.name, d.reason) for d in dropped] == [("orders.nope", "unknown column orders.nope")]


def test_a_computed_field_must_be_a_row_expression():
    fields = [{"name": "loss_amount", "expression": {"sql": "loss_amount"}, "datatype": "Decimal"},
              {"name": "half", "expression": {"sql": "loss_amount / 2"}, "datatype": "Decimal"},
              {"name": "agg", "expression": {"sql": "sum(loss_amount)"}, "datatype": "Decimal"},
              {"name": "evil", "expression": {"sql": "getenv('HOME')"}, "datatype": "String"},
              {"name": "sub", "expression": {"sql": "(select 1)"}, "datatype": "Integer"}]
    changes, dropped = _changes(_document(fields=fields))
    assert {d.name for d in dropped} == {"orders.agg", "orders.evil", "orders.sub"}
    half = next(c for c in changes if c.id == "orders.half").payload
    assert half["expression"] == {"sql": "loss_amount / 2", "dialect": "duckdb"}


def test_bad_identifiers_are_dropped():
    fields = [{"name": "ok", "expression": {"sql": "ok"}, "datatype": "String"},
              {"name": "bad name; drop", "expression": {"sql": "x"}, "datatype": "String"}]
    changes, dropped = _changes(_document(fields=fields))
    assert [d.name for d in dropped] == ["orders.bad name; drop"]


def test_documents_with_yaml_aliases_are_refused():
    text = "version: '0.2.0.dev0'\nname: x\na: &a [1, 2]\nb: *a\ndatasets: []\n"
    with pytest.raises(ValueError, match="alias"):
        parse_document(text)


def test_documents_that_are_not_ossie_are_refused():
    with pytest.raises(ValueError):
        parse_document("- just\n- a list\n")
    with pytest.raises(ValueError):
        parse_document("version: '0.2.0.dev0'\nname: x\ndatasets: []\n")


def test_a_relationship_on_unknown_columns_is_dropped(model):
    doc = yaml.safe_load(export_model(model))
    doc["relationships"] = [
        {"name": "ghost_from", "from": "orders", "to": "dealers", "from_columns": ["ghost"],
         "to_columns": ["dealer_id"]},
        {"name": "ghost_to", "from": "orders", "to": "dealers", "from_columns": ["dealer_id"],
         "to_columns": ["ghost"]},
        {"name": "orders_dealer", "from": "orders", "to": "dealers",
         "from_columns": ["dealer_id"], "to_columns": ["dealer_id"]},
    ]
    changes, dropped = _changes(yaml.safe_dump(doc))
    reasons = {d.name: d.reason for d in dropped}
    assert reasons == {"ghost_from": "unknown column orders.ghost",
                       "ghost_to": "unknown column dealers.ghost"}
    assert [c.id for c in changes if c.kind == "Relationship"] == ["orders_dealer"]


@pytest.mark.parametrize("form", ["yaml", "json"])
def test_a_deeply_nested_document_is_refused(form):
    depth = 20000
    if form == "yaml":
        text = "version: '0.2'\nname: x\ndatasets: " + "[" * depth + "]" * depth
    else:
        text = json.dumps({"version": "0.2", "name": "x", "datasets": "X"}).replace(
            '"X"', "[" * depth + "]" * depth)
    with pytest.raises(ValueError, match="nested too deeply"):
        parse_document(text)


def test_export_and_import_keep_the_time_field(model):
    from ..engine_tests.conftest import make_field, replace_dataset

    # A second time dimension listed first: the time field is not the first date field.
    orders = model.dataset("orders")
    first = make_field("orders", "aaa_date", "date", DimensionSpec(is_time=True))
    model = replace_dataset(model, "orders", fields=[first, *orders.fields])
    changes, dropped = document_to_changes(parse_document(export_model(model)),
                                           SourcePayload(name="duckdb_demo"))
    orders = next(c for c in changes if c.id == "orders").payload
    assert orders["time_field"] == "order_created_date"


def test_two_datasets_over_one_table_keep_the_first():
    doc = yaml.safe_load(_document())
    import copy

    doc["datasets"].append({**copy.deepcopy(doc["datasets"][0]), "name": "orders_again"})
    changes, dropped = _changes(yaml.safe_dump(doc))
    assert ("orders_again", "main.orders is already the table of orders") in [
        (d.name, d.reason) for d in dropped]
    assert [c.id for c in changes if c.kind == "Dataset"] == ["orders"]
