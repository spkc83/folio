from living_reports.metrics.runner import Result
from living_reports.reports.payload import query_view

META = {"fraud_loss": {"label": "Fraud loss", "format": "currency"}}


def result(dimensions: list[str], rows: list[list]) -> Result:
    return Result(
        columns=[*dimensions, "fraud_loss"], rows=rows, query_hash="h", source_id="duckdb_demo",
        row_count=len(rows), sql="", params=[], metric_versions={"fraud_loss": 1},
        metric_meta=META, dimensions=dimensions,
    )


def test_one_dimension_has_no_series():
    view = query_view("q1", result(["orders.sales_channel"], [["App", 15609548.0]]))
    assert view.series is None
    assert view.points[0].label == "App" and view.points[0].formatted == "$15.6M"


def test_month_and_a_category_split_into_series():
    rows = [["2025-11-01", "App", 1.0], ["2025-11-01", "Web", 2.0], ["2025-12-01", "App", 3.0]]
    view = query_view("q1", result(["orders.order_created_date:month", "orders.sales_channel"], rows))
    assert view.series == "orders.sales_channel"
    assert view.points[1].label == "2025-11-01 · Web"


def test_two_categories_have_no_series():
    rows = [["App", "East", 1.0], ["Web", "West", 2.0]]
    assert query_view("q1", result(["orders.sales_channel", "dealers.region"], rows)).series is None


def test_periods_bounded():
    from datetime import date

    from living_reports.reports.payload import periods
    assert periods(date(2025, 7, 1), date(2025, 10, 1), []) == (
        (date(2025, 7, 1), date(2025, 10, 1)), (date(2025, 4, 1), date(2025, 7, 1))
    )


def _months(first_year: int, count: int) -> list[str]:
    return [f"{first_year + i // 12}-{i % 12 + 1:02d}-01" for i in range(count)]


def test_periods_unbounded_takes_the_last_twelve():
    from datetime import date

    from living_reports.reports.payload import periods
    assert periods(None, None, _months(2024, 24)) == (
        (date(2025, 1, 1), date(2026, 1, 1)), (date(2024, 1, 1), date(2025, 1, 1))
    )
    assert periods(None, None, _months(2024, 23)) is None


def test_periods_refuses_a_mid_month_bound():
    from datetime import date

    from living_reports.reports.payload import periods
    assert periods(date(2025, 7, 15), date(2025, 10, 1), []) is None
    assert periods(date(2025, 7, 1), None, _months(2024, 24)) is None


def test_period_label():
    from datetime import date

    from living_reports.reports.payload import period_label
    assert period_label(date(2025, 1, 1), date(2026, 1, 1)) == "2025"
    assert period_label(date(2025, 7, 1), date(2025, 10, 1)) == "Jul 2025 – Sep 2025"
    assert period_label(date(2025, 3, 1), date(2025, 4, 1)) == "Mar 2025"


def test_payload_carries_cube_and_defs():
    from living_reports.reports.explore import CubeView, MetricAgg
    from living_reports.reports.payload import ReportPayload
    from living_reports.spec.models import ReportSpec
    spec = ReportSpec(title="t", objective="why", sections=[])
    bare = ReportPayload(id="r", spec=spec, facts={}, queries={}, view="narrative")
    assert bare.cube is None and bare.query_defs == {}
    cube = CubeView(dimensions=["orders.sales_channel", "month"], labels={"orders.sales_channel": "Sales channel"},
                    vocabulary={"orders.sales_channel": "Sales channel"},
                    metrics={"fraud_loss": MetricAgg(kind="sum", num="fraud_loss", format="currency", label="Fraud loss")},
                    columns=["orders.sales_channel", "month", "fraud_loss"], rows=[["App", "2025-01-01", 1.0]],
                    span=(None, None), query_hash="h")
    full = bare.model_copy(update={"cube": cube, "query_defs": {"q1": {"metrics": ["fraud_loss"]}}})
    assert ReportPayload.model_validate(full.model_dump(mode="json")) == full




def test_query_view_carries_labels():
    r = result(["orders.sales_channel"], [["App", 1.0]]).model_copy(
        update={"labels": {"orders.sales_channel": "Sales channel", "fraud_loss": "Fraud loss"}})
    view = query_view("q1", r)
    assert view.labels == {"orders.sales_channel": "Sales channel", "fraud_loss": "Fraud loss"}
    assert query_view("q1", result(["orders.sales_channel"], [["App", 1.0]])).labels == {}
