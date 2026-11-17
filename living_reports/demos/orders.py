"""The compact orders model: the test model of the unit tests. Its data is ``LR_SOURCE_PATH``."""
from pathlib import Path

from living_reports.semantic.demo import seed_demo
from living_reports.semantic.kinds import FilterSpec

from . import Definition, Demo


def _tables(source_path: Path, moment: int) -> dict[str, str]:
    from living_reports.sources.build import demo_tables

    return demo_tables(source_path, moment)


DEMO = Demo(
    name="orders",
    title="Orders and fraud",
    placeholder="Where is fraud loss concentrated?",
    # A repeated dealer would make the many-to-one join from orders count an order twice.
    keys={"orders": ("order_id",), "dealers": ("dealer_id",)},
    seed=seed_demo,
    tables=_tables,
    definition=Definition(
        metric="fraud_loss",
        filter=FilterSpec(member="orders.fraud_type", op="ne", values=["friendly_fraud"]),
        definition="Total loss on orders confirmed as fraud, excluding friendly fraud.",
        title="Fraud loss: exclude friendly fraud",
        reason="Friendly fraud moves to the disputes metric.",
    ),
    questions=Path(__file__).parent.parent / "evals" / "questions.yaml",
)
