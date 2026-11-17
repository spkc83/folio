"""Shared fixtures: a small orders file with the same 23 columns as the demo data."""
import json
import os
from pathlib import Path

import duckdb
import pytest

REAL_DATA = Path("~/learn/Data/fraud_demo_orders_1m.parquet").expanduser()

ORDERS_SQL = """
SELECT
  'O' || i AS order_id,
  CASE WHEN i >= 2950 THEN TIMESTAMP '2025-12-25' + INTERVAL (i - 2950) HOUR
       ELSE TIMESTAMP '2024-01-01' + INTERVAL (i * 6) HOUR END AS order_created_date,
  CASE WHEN i >= 2950 THEN 'Marketplace'
       ELSE ['App', 'Web', 'Telesales'][1 + i % 3] END AS sales_channel,
  ['East', 'West'][1 + (i % 7) % 2] AS region,
  'D' || (i % 7) AS dealer_id,
  ['New', '3Y+'][1 + i % 2] AS tenure_band,
  ['Card', 'PayPal'][1 + i % 2] AS payment_method,
  'Mobile' AS phone_line_type,
  30.0::FLOAT AS customer_age,
  100.0 + i % 10 AS order_value,
  1::SMALLINT AS num_items,
  10 AS email_first_seen_days,
  0::SMALLINT AS prior_orders_12m,
  12.0 AS contract_length_months,
  0.5 AS address_match_score,
  1.0 AS secondary_address_distance_km,
  0::SMALLINT AS orders_last_7d,
  0::SMALLINT AS payfail_last_30d,
  1.0 AS exposure_months,
  0::TINYINT AS chargeback_within_60d,
  CASE WHEN i % 20 = 0 THEN 1 ELSE 0 END::TINYINT AS is_fraud,
  CASE WHEN i % 40 = 0 THEN 'friendly_fraud'
       WHEN i % 20 = 0 THEN 'identity_theft' ELSE 'none' END AS fraud_type,
  CASE WHEN i % 20 = 0 THEN 50.0 ELSE 0.0 END AS loss_amount
FROM range(3000) t(i)
"""


@pytest.fixture
def orders_file(tmp_path, monkeypatch):
    path = tmp_path / "orders.parquet"
    duckdb.sql(f"COPY ({ORDERS_SQL}) TO '{path}' (FORMAT PARQUET)")
    monkeypatch.setenv("LR_SOURCE_PATH", str(path))
    monkeypatch.setenv("LR_DATA_DIR", str(tmp_path / "lr"))
    from living_reports.sources.registry import reset_sources
    reset_sources()
    return path


@pytest.fixture
def real_orders(tmp_path_factory, monkeypatch):
    """The real demo file, with one built snapshot store shared by the session."""
    if not REAL_DATA.exists():
        pytest.skip("demo data is not on this host")
    monkeypatch.setenv("LR_SOURCE_PATH", str(REAL_DATA))
    monkeypatch.setenv(
        "LR_DATA_DIR", os.environ.get("LR_TEST_REAL_DIR") or str(_real_dir(tmp_path_factory))
    )
    return REAL_DATA


_REAL_DIR: list = []


def _real_dir(factory):
    if not _REAL_DIR:
        _REAL_DIR.append(factory.mktemp("lr-real"))
    return _REAL_DIR[0]


class FakeLLM:
    """Replays queued outputs in place of the provider call."""

    def __init__(self):
        self.outputs, self.prompts, self.schemas = [], [], []

    def push(self, *outputs):
        self.outputs.extend(outputs)

    def __call__(self, prompt, **kwargs):
        from living_reports.harness.providers import LLMResponse
        self.prompts.append(prompt)
        self.schemas.append(kwargs.get("json_schema"))
        assert self.outputs, "the fake model has no output left"
        out = self.outputs.pop(0)
        if out is None:
            return None
        text = out if isinstance(out, str) else json.dumps(out)
        return LLMResponse(text, kwargs.get("model") or "fake-small", "openai", 100, 20)


@pytest.fixture
def fake_llm(monkeypatch):
    from living_reports.harness import llm
    fake = FakeLLM()
    monkeypatch.setattr(llm, "_generate", fake)
    return fake


@pytest.fixture(autouse=True)
def _orders_demo(monkeypatch):
    """The unit tests use the compact orders model. A demo test sets LR_DEMO itself."""
    monkeypatch.setenv("LR_DEMO", "orders")


@pytest.fixture(autouse=True)
def _fresh_store():
    """Each test gets its own process-wide store, opened under its own LR_DATA_DIR."""
    from living_reports.sources.registry import reset_sources
    from living_reports.store import reset_store
    reset_store()
    reset_sources()
    yield
    reset_store()
    reset_sources()


@pytest.fixture
def store(orders_file):
    from living_reports.semantic.demo import seed_demo
    from living_reports.store import get_store, reset_store
    from living_reports.sources.build import build_demo_source
    reset_store()
    s = get_store()
    seed_demo(s.graph)
    # Every test starts at demo moment 1, as the API does.
    build_demo_source(1)
    return s


@pytest.fixture
def store2(store):
    """The store with the demo source at moment 2 (all rows, the new column)."""
    from living_reports.sources.build import build_demo_source
    build_demo_source(2)
    return store


@pytest.fixture
def real_store(real_orders):
    """A seeded store on the real demo data at moment 2."""
    from living_reports.semantic.demo import seed_demo
    from living_reports.sources.build import build_demo_source
    from living_reports.store import get_store, reset_store
    reset_store()
    s = get_store()
    seed_demo(s.graph)
    build_demo_source(2)
    return s


@pytest.fixture
def demo_source(store):
    from living_reports.sources.registry import source_for
    return source_for("duckdb_demo")


@pytest.fixture
def fg(store):
    return store.graph


def friendly_payload(store) -> dict:
    """The live fraud_loss payload with friendly fraud filtered out (the demo change)."""
    payload = dict(store.graph.slice().node("fraud_loss").payload)
    measure = {**payload["measure"], "filter": {
        "member": "orders.fraud_type", "op": "ne", "values": ["friendly_fraud"]}}
    return {**payload, "measure": measure,
            "definition": "Total loss on orders confirmed as fraud, excluding friendly fraud."}
