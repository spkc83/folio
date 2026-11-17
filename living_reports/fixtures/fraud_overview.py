"""A fixed report for the renderer. It uses no LLM, and all values come from real queries."""
from dataclasses import dataclass

from living_reports.engine import Filter, Query, TimeRange
from living_reports.metrics.runner import Fact, Result, run_query, share_facts, to_facts
from living_reports.semantic.demo import DEMO_SOURCE
from living_reports.spec.models import Bars, Line, Prose, ReportSpec, Section, Tile
from living_reports.store import Store

REPORT_ID = "fraud-overview"


@dataclass
class BuiltReport:
    spec: ReportSpec
    facts: dict[str, Fact]
    queries: dict[str, Result]


def build(store: Store, source_id: str = DEMO_SOURCE) -> BuiltReport:
    queries = {
        "loss": run_query(Query(metrics=["fraud_loss"]), source_id, store),
        "loss_by_type": run_query(
            Query(
                metrics=["fraud_loss"],
                dimensions=["orders.fraud_type"],
                filters=[Filter(member="orders.fraud_type", op="ne", values=["none"])],
            ),
            source_id,
            store,
        ),
        "rate_by_channel": run_query(
            Query(metrics=["fraud_rate"], dimensions=["orders.sales_channel"]), source_id, store
        ),
        "loss_by_month": run_query(
            Query(metrics=["fraud_loss"], time=TimeRange(grain="month")), source_id, store
        ),
    }
    facts = [
        *to_facts(queries["loss"], "loss"),
        *to_facts(queries["loss_by_type"], "loss_by_type"),
        *share_facts(queries["loss_by_type"], "fraud_loss", "loss_by_type"),
        *to_facts(queries["rate_by_channel"], "rate_by_channel"),
        *to_facts(queries["loss_by_month"], "loss_by_month"),
    ]
    by_id = {f.id: f for f in facts}
    top_type = queries["loss_by_type"].rows[0][0]
    top_channel, low_channel = (
        queries["rate_by_channel"].rows[0][0],
        queries["rate_by_channel"].rows[-1][0],
    )
    top_label = str(top_type).replace("_", " ")
    spec = ReportSpec(
        title=f"{top_label.capitalize()} is the largest source of fraud loss",
        kicker="Fraud loss, all dates in the data",
        objective="why",
        sections=[
            Section(
                id="overview",
                heading="Where the loss is",
                intent="Find where fraud loss is concentrated.",
                blocks=[
                    Tile(fact_id="loss.fraud_loss", label="Fraud loss"),
                    Tile(
                        fact_id=f"loss_by_type.fraud_loss.share.{top_type}",
                        label=f"Share from {top_label}",
                    ),
                    Tile(
                        fact_id=f"rate_by_channel.fraud_rate.{top_channel}",
                        label=f"Fraud rate in {top_channel}",
                    ),
                    Prose(
                        text=(
                            f"Fraud loss reached {{fact:loss.fraud_loss}}. {top_label.capitalize()} "
                            f"accounts for {{fact:loss_by_type.fraud_loss.share.{top_type}}} of it. "
                            f"{top_channel} has the highest fraud rate at "
                            f"{{fact:rate_by_channel.fraud_rate.{top_channel}}}, against "
                            f"{{fact:rate_by_channel.fraud_rate.{low_channel}}} in {low_channel}."
                        )
                    ),
                    Bars(query_ref="rate_by_channel", title="Fraud rate by sales channel"),
                    Bars(query_ref="loss_by_type", title="Fraud loss by type"),
                    Line(query_ref="loss_by_month", title="Fraud loss by month"),
                ],
            )
        ],
    )
    return BuiltReport(spec=spec, facts=by_id, queries=queries)
