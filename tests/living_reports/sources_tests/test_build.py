import os
import threading

import duckdb
import pytest

from living_reports.sources import DuckDBSource, rows_of
from living_reports.sources import build as build_module
from living_reports.sources.build import (
    BuildError,
    build_demo_source,
    build_duckdb_file,
    demo_moment,
    demo_source_path,
)


def _count(path, table="main.orders"):
    s = DuckDBSource("t", path)
    try:
        return rows_of(s.run(f"SELECT count(*) FROM {table}"))[0][0]
    finally:
        s.close()


def _columns(path, table):
    with duckdb.connect(str(path), read_only=True) as conn:
        return [r[0] for r in conn.execute(f"DESCRIBE {table}").fetchall()]


def test_build_replaces_the_file_atomically(tmp_path):
    target = tmp_path / "x.duckdb"
    build_duckdb_file(target, {"orders": "SELECT * FROM range(3) r(i)"})
    source = DuckDBSource("x", target)
    assert rows_of(source.run("SELECT count(*) FROM main.orders")) == [[3]]
    build_duckdb_file(target, {"orders": "SELECT * FROM range(5) r(i)"})
    assert list(tmp_path.glob("*.tmp")) == []
    assert rows_of(source.run("SELECT count(*) FROM main.orders")) == [[5]]
    source.close()


def test_a_repeated_key_fails_the_build_and_leaves_no_file(tmp_path):
    target = tmp_path / "x.duckdb"
    with pytest.raises(BuildError, match="dealers has more than one row for a dealer_id"):
        build_duckdb_file(
            target,
            {"dealers": "SELECT * FROM (VALUES ('D0', 'East'), ('D0', 'West')) t(dealer_id, region)"},
            keys={"dealers": ("dealer_id",)},
        )
    assert list(tmp_path.iterdir()) == []


def test_demo_moment_1_excludes_late_rows_and_held_column(store):
    path = build_demo_source(1)
    assert path == demo_source_path()
    assert _count(path) == 2800
    assert _count(path, "main.dealers") == 7
    columns = _columns(path, "orders")
    assert "contract_length_months" not in columns and "region" not in columns
    assert _columns(path, "dealers") == ["dealer_id", "region"]
    assert demo_moment(store) == 1


def test_demo_moment_2_has_everything(store):
    path = build_demo_source(2)
    assert _count(path) == 3000
    assert "contract_length_months" in _columns(path, "orders")
    assert demo_moment(store) == 2


def test_build_is_idempotent_and_rebuilds_on_source_change(store, orders_file):
    path = build_demo_source(1)
    stamp = path.stat().st_mtime_ns
    build_demo_source(1)
    assert path.stat().st_mtime_ns == stamp
    os.utime(orders_file, (2_000_000_000, 2_000_000_000))
    build_demo_source(1)
    assert path.stat().st_mtime_ns != stamp


def test_concurrent_first_use_builds_once(store, monkeypatch):
    path = demo_source_path()
    path.unlink(missing_ok=True)
    calls = []
    real = build_module.build_duckdb_file

    def counting(*args, **kwargs):
        calls.append(args)
        return real(*args, **kwargs)

    monkeypatch.setattr(build_module, "build_duckdb_file", counting)
    threads = [threading.Thread(target=build_demo_source, args=(1,)) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(calls) == 1
    assert _count(path) == 2800


def test_a_dealer_with_two_regions_fails_the_demo_build(store, orders_file, tmp_path):
    # D0 gets East on even rows and West on odd rows: the join from orders would count
    # each D0 order twice.
    bad = tmp_path / "bad.parquet"
    duckdb.sql(
        f"COPY (SELECT * EXCLUDE (i) REPLACE (CASE WHEN dealer_id = 'D0' AND i % 2 = 1 "
        f"THEN 'West' ELSE region END AS region) FROM (SELECT *, row_number() OVER () AS i "
        f"FROM '{orders_file}')) TO '{bad}' (FORMAT PARQUET)"
    )
    os.replace(bad, orders_file)
    os.utime(orders_file, (2_000_000_000, 2_000_000_000))
    with pytest.raises(BuildError, match="dealers has more than one row for a dealer_id"):
        build_demo_source(1)
    assert list(demo_source_path().parent.glob("*.tmp")) == []
