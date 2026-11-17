import pytest
from fastapi.testclient import TestClient

from living_reports.api.app import create_app
from living_reports.harness.builder import UNAVAILABLE
from living_reports.store import get_store

from .conftest import AUTHOR, OWNER, use_source

ROUTED = {"objective": "why",
          "query": {"metrics": ["fraud_loss"], "dimensions": ["orders.sales_channel"]},
          "clarify": None, "assumptions": [], "out_of_scope": None}
WORDS = {"title": "Loss by channel", "heading": "Channels",
         "paragraphs": ["Loss reached {fact:q1_total.fraud_loss}."], "action": None}
N_PROPOSED = 32


@pytest.fixture
def client(monkeypatch, tmp_path, keyed_file):
    use_source(monkeypatch, tmp_path, keyed_file)
    with TestClient(create_app()) as c:
        yield c


def _discover(client, fake_llm, draft):
    fake_llm.push(draft.model_dump())
    r = client.post("/api/model/discover", json={"source_id": "test"}, headers=OWNER)
    assert r.status_code == 200, r.text
    return r.json()


def _pid(client, node_id):
    views = client.get("/api/model/proposals").json()
    return next(v["proposal"]["id"] for v in views if v["node_id"] == node_id)


def test_the_app_seeds_no_demo_model_without_the_demo_source(client):
    assert get_store().graph.slice().node("duckdb_demo") is None


def test_discover_needs_an_owner(client, fake_llm, draft):
    r = client.post("/api/model/discover", json={"source_id": "test"}, headers=AUTHOR)
    assert r.status_code == 403 and r.json()["detail"] == "Only an owner can change the model."
    assert len(_discover(client, fake_llm, draft)["proposal_ids"]) == N_PROPOSED


def test_discover_unknown_source_is_404(client):
    r = client.post("/api/model/discover", json={"source_id": "ghost"}, headers=OWNER)
    assert r.status_code == 404


def test_discover_without_a_model_is_503(client, fake_llm):
    fake_llm.push(None)
    r = client.post("/api/model/discover", json={"source_id": "test"}, headers=OWNER)
    assert r.status_code == 503 and r.json() == {"detail": UNAVAILABLE}


def test_discover_with_invalid_output_is_502(client, fake_llm):
    fake_llm.push("not json", "not json", "not json")
    r = client.post("/api/model/discover", json={"source_id": "test"}, headers=OWNER)
    assert r.status_code == 502


def test_proposals_list_and_single_confirm_conflict(client, fake_llm, draft):
    _discover(client, fake_llm, draft)
    views = client.get("/api/model/proposals", headers=AUTHOR).json()
    assert len(views) == N_PROPOSED
    assert sum(1 for v in views if v["probe"]) == 5
    pid = _pid(client, "fraud_loss")
    r = client.post("/api/model/proposals/confirm", json={"ids": [pid]}, headers=OWNER)
    assert r.status_code == 409 and r.json() == {"detail": "Confirm orders.loss_amount first"}


def test_confirm_needs_an_owner(client, fake_llm, draft):
    _discover(client, fake_llm, draft)
    r = client.post("/api/model/proposals/confirm", json={"ids": None}, headers=AUTHOR)
    assert r.status_code == 403
    assert len(client.get("/api/model/proposals").json()) == N_PROPOSED


def test_confirm_all_then_ask_answers_from_the_new_model(client, fake_llm, draft):
    from living_reports.sources.registry import active_source

    _discover(client, fake_llm, draft)
    r = client.post("/api/model/proposals/confirm", json={"ids": None}, headers=OWNER)
    assert r.status_code == 200 and r.json()["failed"] == []
    assert len(r.json()["confirmed"]) == N_PROPOSED
    assert client.get("/api/model/proposals").json() == []
    assert active_source(get_store()) == "test"
    fake_llm.push(ROUTED, WORDS)
    answer = client.post("/api/ask", json={"question": "Where is the fraud loss?"}).json()
    assert answer["kind"] == "report", answer
    report = client.get(f"/api/reports/{answer['report_id']}").json()
    assert report["facts"]["q1_total.fraud_loss"]["formatted"] == "$7.5K"


def test_rename_and_reject_endpoints(client, fake_llm, draft):
    _discover(client, fake_llm, draft)
    pid = _pid(client, "orders.sales_channel")
    r = client.post(f"/api/model/proposals/{pid}/rename", json={"label": "Channel"}, headers=AUTHOR)
    assert r.status_code == 403
    r = client.post(f"/api/model/proposals/{pid}/rename", json={"label": "Channel"}, headers=OWNER)
    assert r.status_code == 200 and r.json()["change"]["payload"]["label"] == "Channel"
    r = client.post(f"/api/model/proposals/{_pid(client, 'orders')}/rename",
                    json={"label": "X"}, headers=OWNER)
    assert r.status_code == 422
    rel = _pid(client, "orders_dealer")
    r = client.post(f"/api/model/proposals/{rel}/reject", json={"reason": "no"}, headers=OWNER)
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    r = client.post(f"/api/model/proposals/{rel}/reject", json={"reason": "no"}, headers=OWNER)
    assert r.status_code == 409
    assert client.post("/api/model/proposals/nope/rename", json={"label": "X"},
                       headers=OWNER).status_code == 404


def test_certify_endpoint(client, fake_llm, draft):
    from living_reports.engine.expressions import parse_expression

    _discover(client, fake_llm, draft)
    client.post("/api/model/proposals/confirm", json={"ids": None}, headers=OWNER)
    r = client.post("/api/model/metrics/fraud_loss/certify", headers=OWNER)
    assert r.status_code == 422
    store = get_store()
    expr = parse_expression("sum(orders.loss_amount)", "orders", store.graph.model())
    store.graph.put("net_expr", "MetricBinding", {
        "name": "net_expr", "label": "Net", "format": "currency", "certified": False,
        "expression": expr.model_dump(mode="json")})
    assert client.post("/api/model/metrics/net_expr/certify", headers=AUTHOR).status_code == 403
    r = client.post("/api/model/metrics/net_expr/certify", headers=OWNER)
    assert r.status_code == 200 and r.json() == {"id": "net_expr", "version": 2, "certified": True}


@pytest.mark.parametrize("kind", ["Source", "Dataset", "Relationship"])
def test_the_graph_proposal_allowlist_still_refuses_mapping_kinds(client, kind):
    r = client.post("/api/graph/proposals", json={
        "kind": kind, "payload": {"name": "x"}, "reason": "r"}, headers=OWNER)
    assert r.status_code == 422 and "cannot be proposed here" in r.json()["detail"]


def _fixture_export():
    """The Ossie export of the discovery fixture model (the draft, confirmed)."""
    from living_reports.discovery.preview import model_from_changes
    from living_reports.discovery.propose import to_changes
    from living_reports.semantic.kinds import SourcePayload
    from living_reports.semantic.ossie import export_model

    from .conftest import make_catalog, make_draft

    changes, _ = to_changes(make_draft(), make_catalog(), SourcePayload(name="test"))
    return export_model(model_from_changes(changes))


def test_export_endpoint(client, fake_llm, draft):
    import yaml

    assert client.get("/api/model/export").status_code == 404  # nothing confirmed yet
    _discover(client, fake_llm, draft)
    client.post("/api/model/proposals/confirm", json={"ids": None}, headers=OWNER)
    r = client.get("/api/model/export", headers=AUTHOR)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/yaml")
    assert 'filename="test.ossie.yaml"' in r.headers["content-disposition"]
    doc = yaml.safe_load(r.text)
    assert doc["name"] == "test" and doc["version"] == "0.2.0.dev0"
    assert client.get("/api/model/export?source_id=ghost").status_code == 404


def test_import_endpoint_proposes_and_probes(client):
    r = client.post("/api/model/import", json={"source_id": "test", "document": _fixture_export()},
                    headers=OWNER)
    assert r.status_code == 200, r.text
    body = r.json()
    # source, 2 datasets, 7 orders fields + 2 dealers fields, 1 relationship, 4 metrics
    assert len(body["run"]["proposal_ids"]) == 17 and body["dropped"] == []
    views = client.get("/api/model/proposals").json()
    loss = next(v for v in views if v["node_id"] == "fraud_loss")
    assert loss["probe"]["value"] == "$7.5K"
    # Four eyes: the importer is the proposer, so the importer cannot confirm the import.
    assert loss["proposal"]["proposed_by"] == "omar"
    assert loss["proposal"]["reason"] == f"import:{body['run']['id']} (by omar)"
    assert loss["run_id"] == body["run"]["id"]
    r = client.post("/api/model/proposals/confirm", json={"ids": None}, headers=OWNER)
    failed = r.json()["failed"]
    assert len(failed) == 17 and {why for _, why in failed} == {
        "The person who proposed a change cannot decide on it."}


def test_import_drops_what_fails_its_probe(client):
    import json

    import yaml

    doc = yaml.safe_load(_fixture_export())
    loss = next(m for m in doc["metrics"] if m["name"] == "fraud_loss")
    data = json.loads(loss["custom_extensions"][0]["data"])
    data["measure"]["field"] = "orders.fraud_type"  # a sum over text fails on the data
    loss["custom_extensions"][0]["data"] = json.dumps(data)
    body = client.post("/api/model/import", json={"source_id": "test",
                                                  "document": yaml.safe_dump(doc)},
                       headers=OWNER).json()
    assert [d["name"] for d in body["dropped"]] == ["fraud_loss"]
    assert "fraud_loss" not in {v["node_id"] for v in client.get("/api/model/proposals").json()}


def test_import_bad_version_is_422(client):
    text = _fixture_export().replace("version: 0.2.0.dev0", "version: '1.0'")
    r = client.post("/api/model/import", json={"source_id": "test", "document": text},
                    headers=OWNER)
    assert r.status_code == 422 and "0.2" in r.json()["detail"]


def test_import_size_limit(client):
    r = client.post("/api/model/import", json={"source_id": "test", "document": "#" * 1_000_001},
                    headers=OWNER)
    assert r.status_code == 422 and "1 MB" in r.json()["detail"]


def test_import_needs_an_owner(client):
    r = client.post("/api/model/import", json={"source_id": "test", "document": _fixture_export()},
                    headers=AUTHOR)
    assert r.status_code == 403
    assert client.get("/api/model/proposals").json() == []


def test_import_unknown_source_is_404(client):
    r = client.post("/api/model/import", json={"source_id": "ghost", "document": "x"},
                    headers=OWNER)
    assert r.status_code == 404


@pytest.fixture
def demo_client(orders_file):
    with TestClient(create_app()) as c:
        yield c


def test_model_confirm_refuses_a_definition_change(demo_client):
    from ..conftest import friendly_payload

    p = demo_client.post("/api/graph/proposals", json={
        "node_id": "fraud_loss", "kind": "MetricBinding", "payload": friendly_payload(get_store()),
        "reason": "Exclude friendly fraud."}, headers=AUTHOR).json()
    r = demo_client.post("/api/model/proposals/confirm", json={"ids": [p["id"]]}, headers=OWNER)
    assert r.status_code == 409 and r.json() == {"detail": "Not a model proposal"}
    r = demo_client.post(f"/api/model/proposals/{p['id']}/rename", json={"label": "X"},
                         headers=OWNER)
    assert r.status_code == 422
    assert get_store().graph.proposal(p["id"]).status == "pending"


def test_both_routes_confirm_through_one_helper(demo_client, monkeypatch):
    from living_reports.reports import changes

    from ..conftest import friendly_payload

    seen = []
    real = changes.confirm_proposal

    def spy(proposal, by, store, context):
        seen.append(proposal.id)
        return real(proposal, by, store, context)

    monkeypatch.setattr(changes, "confirm_proposal", spy)
    p = demo_client.post("/api/graph/proposals", json={
        "node_id": "fraud_loss", "kind": "MetricBinding", "payload": friendly_payload(get_store()),
        "reason": "Exclude friendly fraud."}, headers=AUTHOR).json()
    r = demo_client.post(f"/api/graph/proposals/{p['id']}/confirm", headers=OWNER)
    assert r.status_code == 200 and seen == [p["id"]]


def test_the_renamer_cannot_confirm_over_the_api(client, fake_llm, draft):
    _discover(client, fake_llm, draft)
    pid = _pid(client, "orders.sales_channel")
    new = client.post(f"/api/model/proposals/{pid}/rename", json={"label": "Channel"},
                      headers=OWNER).json()
    r = client.post("/api/model/proposals/confirm", json={"ids": [new["id"]]}, headers=OWNER)
    assert r.status_code == 409
    assert r.json()["detail"] == "The person who proposed a change cannot decide on it."


def test_import_refreshes_a_cached_catalog_first(client):
    from living_reports.sources.registry import source_for

    source = source_for("test")
    calls = []
    source.refresh = lambda: calls.append("refresh")
    r = client.post("/api/model/import", json={"source_id": "test", "document": _fixture_export()},
                    headers=OWNER)
    assert r.status_code == 200 and calls == ["refresh"]


def test_reject_refuses_a_definition_change(demo_client):
    from ..conftest import friendly_payload

    p = demo_client.post("/api/graph/proposals", json={
        "node_id": "fraud_loss", "kind": "MetricBinding", "payload": friendly_payload(get_store()),
        "reason": "Exclude friendly fraud."}, headers=AUTHOR).json()
    r = demo_client.post(f"/api/model/proposals/{p['id']}/reject", json={"reason": "no"},
                         headers=OWNER)
    assert r.status_code == 409 and r.json() == {"detail": "Not a model proposal"}
    assert get_store().graph.proposal(p["id"]).status == "pending"


def test_unknown_ids_are_404(client):
    for url, body in (("/api/model/proposals/confirm", {"ids": ["nope"]}),
                      ("/api/model/proposals/nope/reject", {"reason": "x"}),
                      ("/api/model/proposals/nope/rename", {"label": "x"})):
        assert client.post(url, json=body, headers=OWNER).status_code == 404, url


def test_export_filename_is_safe(client, monkeypatch):
    from living_reports.api import routes_model

    assert routes_model.attachment_name('a"b\r\nc;d') == 'a_b__c_d.ossie.yaml'


def _imported_then_renamed_by_ana(client):
    from living_reports.discovery.confirm import rename_proposal

    body = client.post("/api/model/import", json={"source_id": "test",
                                                  "document": _fixture_export()},
                       headers=OWNER).json()
    assert body["dropped"] == []
    return rename_proposal(_pid(client, "orders.sales_channel"), "Channel", "ana", get_store())


@pytest.mark.parametrize("action", ["confirm", "reject"])
def test_the_graph_route_enforces_the_lineage(client, action):
    new = _imported_then_renamed_by_ana(client)
    body = {} if action == "confirm" else {"reason": "mine"}
    r = client.post(f"/api/graph/proposals/{new.id}/{action}", json=body, headers=OWNER)
    assert r.status_code == 403
    assert r.json()["detail"] == "The person who proposed a change cannot decide on it."
    assert get_store().graph.proposal(new.id).status == "pending"


def test_the_model_reject_enforces_the_lineage(client):
    new = _imported_then_renamed_by_ana(client)
    r = client.post(f"/api/model/proposals/{new.id}/reject", json={"reason": "mine"},
                    headers=OWNER)
    assert r.status_code == 403
    assert get_store().graph.proposal(new.id).status == "pending"


def test_the_shared_helpers_enforce_the_lineage(client):
    from living_reports.reports.changes import confirm_proposal, reject_proposal
    from living_reports.reports.signoff import Forbidden

    new = _imported_then_renamed_by_ana(client)
    store = get_store()
    with pytest.raises(Forbidden):
        confirm_proposal(new, "omar", store, lambda: None)
    with pytest.raises(Forbidden):
        reject_proposal(new, "omar", "mine", store)
    assert store.graph.proposal(new.id).status == "pending"


def test_a_second_owner_confirms_what_omar_imported(client):
    """The demo has two owners, so the four-eyes rule leaves a path to confirm."""
    client.post("/api/model/import", json={"source_id": "test", "document": _fixture_export()},
                headers=OWNER)
    pid = _pid(client, "orders.sales_channel")
    r = client.post("/api/model/proposals/confirm", json={"ids": None},
                    headers={"X-LR-User": "priya"})
    assert r.status_code == 200, r.text
    assert r.json()["failed"] == []
    assert get_store().graph.proposal(pid).status == "confirmed"
