"""Run the checks for an edition."""
from dataclasses import dataclass
from pathlib import Path

from living_reports.checker.steps import CheckResult, check_rules, check_totals, check_trace
from living_reports.checker.wording import challenge_wording
from living_reports.reports.editions import Edition
from living_reports.semantic.graph import FolioGraph

RULES = str(Path(__file__).parent / "rules.yaml")


@dataclass
class CheckContext:
    graph: FolioGraph
    rules_path: str = RULES


def run_checks(edition: Edition, ctx: CheckContext) -> list[CheckResult]:
    return [
        check_trace(edition),
        check_totals(edition, ctx.graph),
        check_rules(edition, ctx.graph, ctx.rules_path),
    ]


def run_all(edition: Edition, ctx: CheckContext) -> list[CheckResult]:
    return [*run_checks(edition, ctx), challenge_wording(edition)]


def deterministic_pass(results: list[CheckResult]) -> bool:
    return all(r.status == "pass" for r in results if r.step != "wording")
