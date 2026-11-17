import pytest
from fastapi.testclient import TestClient

from living_reports.api.app import create_app
from living_reports.harness import progress

ROUTED = {"objective": "status", "query": {"metrics": ["order_count"], "dimensions": ["dealers.region"]},
          "clarify": None, "assumptions": [], "out_of_scope": None}
WORDS = {"title": "Orders by region", "heading": "Volume",
         "paragraphs": ["Orders reached {fact:q1_total.order_count}."], "action": None}


@pytest.fixture(autouse=True)
def _clean():
    progress.clear()
    yield
    progress.clear()


def test_a_step_closes_the_stage_before_it():
    progress.track("p-12345678", "t1")
    progress.step("t1", "read")
    progress.step("t1", "query")
    view = progress.snapshot("p-12345678")
    assert [s.key for s in view.steps] == ["understand", "numbers"]
    assert [s.label for s in view.steps] == ["Understand your question", "Get the numbers"]
    assert view.steps[0].seconds is not None and view.steps[1].seconds is None
    assert not view.done


def test_steps_of_one_stage_show_as_one_row():
    progress.track("p-12345678", "t1")
    for key in ["read", "decide", "route", "query", "brief"]:
        progress.step("t1", key)
    for _ in range(3):  # the writer loop: draft, check, draft again
        progress.step("t1", "write")
        progress.step("t1", "check")
    view = progress.snapshot("p-12345678")
    assert [s.key for s in view.steps] == ["understand", "numbers", "write"]
    assert view.steps[-1].seconds is None


def test_a_second_try_does_not_go_back_to_an_earlier_stage():
    progress.track("p-12345678", "t1")
    for key in ["read", "route", "query", "brief", "write", "check", "escalate",
                "read", "route", "query", "brief", "write", "check", "save"]:
        progress.step("t1", key)
    progress.finish("t1")
    view = progress.snapshot("p-12345678")
    assert [s.key for s in view.steps] == ["understand", "numbers", "write", "save"]


def test_no_stage_label_names_the_machinery():
    words = " ".join(label for _, label in progress.STAGES).lower()
    for word in ["model", "decision", "sql", "query", "llm", "token", "rubric", "larger"]:
        assert word not in words


def test_finish_closes_the_last_step():
    progress.track("p-12345678", "t1")
    progress.step("t1", "read")
    progress.finish("t1")
    view = progress.snapshot("p-12345678")
    assert view.done and view.steps[0].seconds is not None


def test_a_turn_with_no_progress_id_records_nothing():
    progress.track(None, "t1")
    progress.step("t1", "read")
    assert progress.snapshot("t1") is None


def test_an_unknown_progress_id_has_no_snapshot():
    assert progress.snapshot("p-unknown1") is None


def test_old_runs_are_removed(monkeypatch):
    progress.track("p-old00001", "t-old")
    later = progress._now() + progress.KEEP_SECONDS + 1
    monkeypatch.setattr(progress, "_now", lambda: later)
    progress.track("p-new00001", "t-new")
    assert progress.snapshot("p-old00001") is None
    assert progress.snapshot("p-new00001") is not None


def test_a_running_question_cannot_start_again_until_it_ends():
    key = progress.duplicate_key("ana", "demo", None, "Fraud  loss by REGION")
    assert key == progress.duplicate_key("ana", "demo", None, "fraud loss by region")
    assert progress.start("t1", key)
    assert not progress.start("t2", key)
    assert progress.start("t3", progress.duplicate_key("ben", "demo", None, "fraud loss by region"))
    progress.finish("t1")
    assert progress.start("t4", key)


@pytest.fixture
def client(orders_file):
    with TestClient(create_app()) as c:
        yield c


def test_an_answer_records_its_steps(client, fake_llm):
    fake_llm.push(ROUTED, WORDS)
    r = client.post("/api/ask", json={"question": "How are we doing?", "progress_id": "p-abcdef12"})
    assert r.json()["kind"] == "report"
    view = client.get("/api/progress/p-abcdef12").json()
    assert view["done"] is True
    keys = [s["key"] for s in view["steps"]]
    assert keys == ["understand", "numbers", "write", "save"]
    assert all(s["seconds"] is not None for s in view["steps"])


def test_an_exploration_records_its_steps(client, fake_llm):
    fake_llm.push({"sql": "select 1 as one"})
    client.post("/api/explore", json={"question": "x", "progress_id": "p-abcdef13"})
    keys = [s["key"] for s in client.get("/api/progress/p-abcdef13").json()["steps"]]
    assert keys == ["numbers", "save"]


def test_an_unknown_progress_id_is_not_found(client):
    assert client.get("/api/progress/p-nothing1").status_code == 404


def test_a_question_that_is_running_is_refused(client, fake_llm):
    from living_reports.sources.registry import active_source
    from living_reports.store import get_store

    from living_reports.api.routes_ask import AskRequest, duplicate_key

    key = duplicate_key(AskRequest(question="how are  we doing?"), "ana", active_source(get_store()))
    assert progress.start("t-running", key)
    r = client.post("/api/ask", json={"question": "How are we doing?"})
    assert r.status_code == 409 and "already" in r.json()["detail"]
    progress.finish("t-running")
    fake_llm.push(ROUTED, WORDS)
    assert client.post("/api/ask", json={"question": "How are we doing?"}).json()["kind"] == "report"


def test_a_progress_id_must_be_short_and_plain(client):
    r = client.post("/api/ask", json={"question": "x", "progress_id": "../../etc"})
    assert r.status_code == 422
