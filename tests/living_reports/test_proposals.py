"""Model-proposed claims: the writer names members it wants to state facts about (``focus``);
code computes their claims with the same rules as for a member that the question names.

The model never writes a claim. A focus label that is not a member of the section is dropped.
"""
from living_reports.harness.claims import claims_for, readable

from .test_claims import by_kind, region_case

# Seven regions: only the top 5 get a rank claim without a focus.
SEVEN = [("South", 15_840_000), ("East", 12_500_000), ("North", 11_200_000), ("West", 9_290_000),
         ("Central", 7_100_000), ("Coast", 3_200_000), ("Islands", 1_100_000)]


def _texts(out, facts, kind):
    return [readable(c.text, out.facts(facts)) for c in by_kind(out.claims, kind)]


def test_a_focus_member_gets_the_claims_of_a_named_member():
    query, results, facts = region_case(SEVEN)
    plain = claims_for(query, "q1", results, facts)
    assert not any("Coast" in t for t in _texts(plain, facts, "rank"))
    focused = claims_for(query, "q1", results, facts, focus=["Coast"])
    assert ("Coast ranked 6th of the 7 regions by fraud loss ($3.2M, 5.31% of the total)"
            in _texts(focused, facts, "rank"))
    assert "South's fraud loss ($15.8M) was 5.0× Coast's ($3.2M)" in _texts(focused, facts, "compare")
    # A proposed member gets claims, not the lead: the claims of the section's story stay first
    # (the insight eval: focus claims took the two covered slots and pushed the story out).
    assert focused.claims[0].id == plain.claims[0].id


def test_a_focus_label_that_is_not_a_member_is_ignored():
    query, results, facts = region_case(SEVEN)
    plain = claims_for(query, "q1", results, facts)
    other = claims_for(query, "q1", results, facts, focus=["Atlantis"])
    assert [c.id for c in other.claims] == [c.id for c in plain.claims]


# --- the writer proposes, code computes, the writer cites ---------------------------------
import re  # noqa: E402

import pytest  # noqa: E402

from living_reports.harness.builder import AskContext, answer  # noqa: E402
from living_reports.reports.store import ReportStore  # noqa: E402

CHANNEL = "orders.sales_channel"
ROUTED = {"objective": "why", "query": {"metrics": ["fraud_loss"], "dimensions": [CHANNEL]},
          "clarify": None, "assumptions": [], "out_of_scope": None}
FIRST = {"title": "Loss", "heading": "Loss", "paragraphs": ["{claim:q1.value.fraud_loss}."],
         "action": None, "focus": ["Marketplace"]}


@pytest.fixture
def ctx(store2):
    return AskContext(store=store2, reports=ReportStore(store2), source_id="duckdb_demo")


def test_a_proposed_member_gets_claims_and_the_writer_cites_them(ctx, fake_llm):
    second = {**FIRST, "focus": [], "paragraphs": ["{claim:q1.value.fraud_loss}."]}
    fake_llm.push(ROUTED, FIRST, second)
    report = ctx.reports.get(answer("Where is the loss?", None, ctx).report_id)
    section = report.spec.sections[0]
    assert section.focus == ["Marketplace"]
    note = fake_llm.prompts[2].split("Code computed these claims for Marketplace:", 1)[1]
    added = re.findall(r"^- (q1\.[\w.]+):", note, re.MULTILINE)
    assert added and all(c in report.spec.claims for c in added)
    assert all("marketplace" in c.lower() for c in added)
    assert ctx.drafts[0] == "proposal"
    # The claims about the proposed member do not take the lead from the section's top claims.
    lead = re.findall(r"\{claim:([\w.]+)\}", [b.text for b in section.blocks if b.type == "prose"][0])
    assert report.spec.claims[lead[0]].segment.get(CHANNEL) != "Marketplace"


def test_a_proposal_of_no_member_changes_nothing(ctx, fake_llm):
    fake_llm.push(ROUTED, {**FIRST, "focus": ["Atlantis"]})
    report = ctx.reports.get(answer("Where is the loss?", None, ctx).report_id)
    assert report.spec.sections[0].focus == [] and len(fake_llm.prompts) == 2


def test_the_writer_proposes_only_once(ctx, fake_llm):
    fake_llm.push(ROUTED, FIRST, {**FIRST, "focus": ["Web"]})
    report = ctx.reports.get(answer("Where is the loss?", None, ctx).report_id)
    assert report.spec.sections[0].focus == ["Marketplace"] and len(fake_llm.prompts) == 3


def test_a_refresh_keeps_the_claims_of_a_focus_member(ctx, fake_llm):
    from living_reports.reports.changes import refresh_report
    second = {**FIRST, "focus": [], "paragraphs": ["{claim:q1.compare.fraud_loss.App.Marketplace}."]}
    fake_llm.push(ROUTED, FIRST, second)
    report = ctx.reports.get(answer("Where is the loss?", None, ctx).report_id)
    cited = [c for c in report.spec.claims if "marketplace" in c.lower() and c in str(report.spec.sections[0].blocks)]
    assert cited == ["q1.compare.fraud_loss.App.Marketplace"]
    assert refresh_report(report, ctx)
    assert report.spec.sections[0].stale_claims == []
    assert all(c in report.spec.claims for c in cited)


# --- a comparison across sections ---------------------------------------------------------
REGION = "dealers.region"
BY_REGION = {**ROUTED, "query": {"metrics": ["fraud_loss"], "dimensions": [REGION]}}
WEB = {**ROUTED, "query": {"metrics": ["fraud_loss"], "dimensions": [REGION],
                           "filters": [{"member": CHANNEL, "op": "eq", "values": ["Web"]}]}}
PLAIN = {"title": "Loss", "heading": "By region", "paragraphs": ["{claim:q1.value.fraud_loss}."],
         "action": None}


def _two_sections(ctx, fake_llm, cross, second_words=None):
    fake_llm.push(BY_REGION, PLAIN)
    rid = answer("Where is the loss?", None, ctx).report_id
    first = {**PLAIN, "heading": "Web by region", "paragraphs": ["{claim:q2.value.fraud_loss}."],
             "cross": cross}
    fake_llm.push(WEB, first, second_words or {**first, "cross": []})
    return ctx.reports.get(answer("And for Web?", rid, ctx).report_id)


def test_a_cross_section_comparison_names_both_scopes_and_is_checked(ctx, fake_llm):
    from living_reports.harness.claims import check
    report = _two_sections(ctx, fake_llm, [{"this": "West", "earlier": "By region: West"}])
    (claim,) = [c for c in report.spec.claims.values() if c.params.get("cross")]
    text = readable(claim.text, report.facts)
    assert "West (all)" in text and "West (Web)" in text
    assert check(claim, report.facts)
    assert "Code computed these claims" in fake_llm.prompts[-1]


def test_a_cross_section_comparison_needs_the_same_period(ctx, fake_llm):
    later = {**WEB, "query": {**WEB["query"], "time": {"start": "2025-01-01", "end": "2025-07-01"}}}
    fake_llm.push(BY_REGION, PLAIN)
    rid = answer("Where is the loss?", None, ctx).report_id
    words = {**PLAIN, "heading": "Web", "paragraphs": ["{claim:q2.value.fraud_loss}."],
             "cross": [{"this": "West", "earlier": "By region: West"}]}
    fake_llm.push(later, words)
    report = ctx.reports.get(answer("And for Web?", rid, ctx).report_id)
    assert not any(c.params.get("cross") for c in report.spec.claims.values())


def test_the_checker_derives_a_cross_section_ratio_again(ctx, fake_llm):
    from living_reports.harness.claims import expected_derived
    report = _two_sections(ctx, fake_llm, [{"this": "West", "earlier": "By region: West"}])
    (claim,) = [c for c in report.spec.claims.values() if c.params.get("cross")]
    base = {k: f for k, f in report.facts.items() if ".derived." not in k}
    again = expected_derived(report.spec, report.query_defs, report.queries, base)
    ratio = claim.params["ratio"]
    assert again[ratio].formatted == report.facts[ratio].formatted
