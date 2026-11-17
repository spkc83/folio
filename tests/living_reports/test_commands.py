"""Section commands: chip re-runs, selections, chart swaps, rewrites, removal, edits."""
import pytest

from living_reports.engine import OutOfScope, Query
from living_reports.engine.query import Filter
from living_reports.harness.builder import AskContext, answer
from living_reports.harness.commands import ref_for, rerun_section
from living_reports.reports.store import ReportStore
from living_reports.spec.models import Prose
from living_reports.spec.validate import validate_spec


@pytest.fixture
def ctx(store2):
    return AskContext(store=store2, reports=ReportStore(store2), source_id="duckdb_demo")


def routed(**kw):
    return {"objective": "why", "query": None, "clarify": None, "assumptions": [], "out_of_scope": None, **kw}


LOSS_BY_CHANNEL = {"metrics": ["fraud_loss"], "dimensions": ["orders.sales_channel"]}
# The first claim (the breakdown's leader) is cited, so code prepends nothing.
PROSE = "{claim:q1.extreme.max.fraud_loss}. App leads at {fact:q1.fraud_loss.App} of {fact:q1_total.fraud_loss}."
WORDS = {"title": "Loss by channel", "heading": "Where the loss is", "paragraphs": [PROSE], "action": None}


@pytest.fixture
def report(ctx, fake_llm):
    fake_llm.push(routed(query=LOSS_BY_CHANNEL), WORDS)
    return ctx.reports.get(answer("Where is the loss?", None, ctx).report_id)


def valid(report):
    return validate_spec(report.spec, report.facts, report.queries)


def prose_of(report, sid="s1"):
    section = next(s for s in report.spec.sections if s.id == sid)
    return [b.text for b in section.blocks if isinstance(b, Prose)]


def east(member="dealers.region", value="East"):
    return Filter(member=member, op="eq", values=[value])


def test_ref_for():
    assert ref_for("s2") == "q2" and ref_for("s13") == "q13"


def test_rerun_replaces_blocks_and_keeps_matching_prose(ctx, fake_llm, report):
    calls = len(fake_llm.prompts)
    before = report.facts["q1_total.fraud_loss"].formatted
    query = Query(metrics=["fraud_loss"], dimensions=["orders.sales_channel"], filters=[east()])
    saved, dropped, stale = rerun_section(report, "s1", query, ctx)
    assert dropped is False and stale is True
    assert saved.query_defs["q1"]["filters"][0]["values"] == ["East"]
    assert prose_of(saved) == [PROSE]
    assert saved.facts["q1_total.fraud_loss"].formatted != before
    assert valid(saved) == []
    assert len(fake_llm.prompts) == calls
    assert ctx.reports.get(report.id).version == report.version + 1


def test_rerun_drops_prose_whose_tokens_vanish(ctx, report):
    query = Query(metrics=["fraud_loss"], dimensions=["dealers.region"])
    saved, dropped, _ = rerun_section(report, "s1", query, ctx)
    assert dropped is True
    assert prose_of(saved) == []
    assert "q1.fraud_loss.App" not in saved.facts
    assert valid(saved) == []
    assert "dealers.region" in saved.cube.dimensions


def test_rerun_with_no_rows_is_rejected_and_changes_nothing(ctx, report):
    query = Query(metrics=["fraud_loss"], filters=[east("orders.sales_channel", "Nope")])
    with pytest.raises(OutOfScope) as error:
        rerun_section(report, "s1", query, ctx)
    assert error.value.reason == "No rows match Sales channel = Nope."
    assert ctx.reports.get(report.id).version == report.version


def test_rerun_unknown_section_is_a_key_error(ctx, report):
    with pytest.raises(KeyError):
        rerun_section(report, "s9", Query(metrics=["fraud_loss"]), ctx)


# Task 3: selections narrow the routed query in code.
from datetime import date  # noqa: E402

from living_reports.harness.commands import Selection, with_selection  # noqa: E402


def test_with_selection_merges_in_order(ctx, report):
    report.query_defs["q1"]["filters"] = [{"member": "dealers.region", "op": "eq", "values": ["East"]}]
    sel = Selection(filters=[east("orders.sales_channel", "Telesales")], range=("2025-07-01", "2025-11-01"), section_id="s1")
    q = with_selection(Query(metrics=["fraud_loss"]), sel, report)
    assert [(f.member, f.values) for f in q.filters] == [("dealers.region", ["East"]), ("orders.sales_channel", ["Telesales"])]
    assert q.time.start == date(2025, 7, 1) and q.time.end == date(2025, 12, 1)


def test_with_selection_from_a_fact_binding(ctx, report):
    q = with_selection(Query(metrics=["fraud_loss"]), Selection(fact_id="q1.fraud_loss.App"), report)
    assert [(f.member, f.op, f.values) for f in q.filters] == [("orders.sales_channel", "eq", ["App"])]
    fact = report.facts["q1.fraud_loss.App"]
    report.facts["q1.fraud_loss.App"] = fact.model_copy(
        update={"binding": fact.binding.model_copy(update={"keys": {"orders.fraud_type": None}})})
    q = with_selection(Query(metrics=["fraud_loss"]), Selection(fact_id="q1.fraud_loss.App"), report)
    assert [(f.member, f.op, f.values) for f in q.filters] == [("orders.fraud_type", "is_null", [])]


def test_with_selection_later_filter_replaces_same_member(ctx, report):
    sel = Selection(fact_id="q1.fraud_loss.App", filters=[east("orders.sales_channel", "Web")])
    routed_query = Query(metrics=["fraud_loss"], filters=[east("orders.sales_channel", "Telesales")])
    q = with_selection(routed_query, sel, report)
    assert [(f.member, f.values) for f in q.filters] == [("orders.sales_channel", ["Web"])]


def test_with_selection_keeps_the_query_time_and_grain(ctx, report):
    sel = Selection(range=("2025-01-01", "2025-01-01"))
    q = with_selection(Query(metrics=["fraud_loss"], time={"grain": "month"}), sel, report)
    assert (q.time.start, q.time.end, q.time.grain) == (date(2025, 1, 1), date(2025, 2, 1), "month")
    assert with_selection(Query(metrics=["fraud_loss"]), None, report) == Query(metrics=["fraud_loss"])


# Task 5: section commands.
from living_reports.harness.builder import run_section  # noqa: E402
from living_reports.harness.commands import (  # noqa: E402
    can_edit,
    chart_forms,
    remove_section,
    rewrite_section,
    swap_chart,
)
from living_reports.spec.models import Bars, Table, Tile  # noqa: E402

FIRST_HALF = {"start": "2025-01-01", "end": "2025-07-01"}


def forms(ctx, **query):
    q = Query(metrics=["fraud_loss"], **query)
    results, _ = run_section(q, "q1", ctx)
    return chart_forms(q, results, "q1")


def test_chart_forms_for_the_demo_shapes(ctx):
    channel = ["orders.sales_channel"]
    assert forms(ctx, dimensions=channel, time=FIRST_HALF) == ["bars", "table", "delta_bars", "segmented"]
    assert forms(ctx, dimensions=channel) == ["bars", "table", "segmented"]
    assert forms(ctx, time={"grain": "month"}) == ["table", "line"]
    three = Filter(member="orders.sales_channel", op="in", values=["App", "Web", "Telesales"])
    assert forms(ctx, dimensions=channel, filters=[three], time={"grain": "month"}) == ["table", "line", "small_multiples"]
    rate = Query(metrics=["fraud_rate"], dimensions=channel)
    results, _ = run_section(rate, "q1", ctx)
    assert "segmented" not in chart_forms(rate, results, "q1")
    assert forms(ctx) == []


def test_swap_chart_replaces_one_block(ctx, fake_llm, report):
    calls = len(fake_llm.prompts)
    saved = swap_chart(report, "s1", "table", ctx)
    blocks = saved.spec.sections[0].blocks
    assert [type(b) for b in blocks] == [Prose if isinstance(b, Prose) else Tile if isinstance(b, Tile) else Table
                                         for b in report.spec.sections[0].blocks]
    assert [b for b in blocks if isinstance(b, Table)][0].title == "Fraud loss by Sales channel"
    assert not [b for b in blocks if isinstance(b, Bars)]
    assert valid(saved) == [] and len(fake_llm.prompts) == calls
    with pytest.raises(ValueError, match="line does not fit this query; choose one of: bars, table, segmented"):
        swap_chart(saved, "s1", "line", ctx)


def test_shorter_keeps_tokens_and_shrinks(ctx, fake_llm, report):
    calls = len(fake_llm.prompts)
    fake_llm.push({"paragraphs": ["Total {fact:q1_total.fraud_loss}."]})
    saved = rewrite_section(report, "s1", "shorter", ctx)
    assert prose_of(saved) == ["Total {fact:q1_total.fraud_loss}."]
    assert len(fake_llm.prompts) == calls + 1
    prompt = fake_llm.prompts[-1]
    assert "<data>" in prompt and PROSE in prompt and "fewer words" in prompt
    assert valid(saved) == [] and "ana" in saved.contributors


def test_rewrite_schema_is_bounded(ctx, fake_llm, report):
    fake_llm.push({"paragraphs": ["Total {fact:q1_total.fraud_loss}."]})
    rewrite_section(report, "s1", "shorter", ctx)
    schema = fake_llm.schemas[-1]["properties"]["paragraphs"]
    assert schema["maxItems"] < 2000 and schema["items"]["maxLength"] < 2000


def test_expand_adds_one_paragraph(ctx, fake_llm, report):
    fake_llm.push({"paragraphs": ["App carries {fact:q1.fraud_loss.App}."]})
    saved = rewrite_section(report, "s1", "expand", ctx)
    assert prose_of(saved) == [PROSE, "App carries {fact:q1.fraud_loss.App}."]


def test_shorter_that_grows_fails_and_changes_nothing(ctx, fake_llm, report):
    long = {"paragraphs": [PROSE + " " + PROSE]}
    fake_llm.push(long, long, long)
    assert rewrite_section(report, "s1", "shorter", ctx) is None
    assert ctx.reports.get(report.id).version == report.version
    assert "longer" in fake_llm.prompts[-1]


def test_regenerate_with_a_raw_number_is_repaired(ctx, fake_llm, report):
    fake_llm.push({"paragraphs": ["Loss rose 38%."]}, {"paragraphs": ["Loss reached {fact:q1_total.fraud_loss}."]})
    saved = rewrite_section(report, "s1", "regenerate", ctx)
    assert prose_of(saved) == ["Loss reached {fact:q1_total.fraud_loss}."]
    assert "raw_number" in fake_llm.prompts[-1]


def test_remove_section_keeps_ids_stable(ctx, fake_llm, report):
    fake_llm.push(routed(query={"metrics": ["order_count"], "dimensions": ["dealers.region"]}),
                  {**WORDS, "paragraphs": ["Orders: {fact:q2_total.order_count}."]})
    answer("Orders by region?", report.id, ctx)
    saved = remove_section(ctx.reports.get(report.id), "s1", ctx)
    assert [s.id for s in saved.spec.sections] == ["s2"]
    assert "q1" not in saved.query_defs
    assert not [k for k in saved.facts if k.startswith(("q1.", "q1_"))]
    assert not [k for k in saved.queries if k == "q1" or k.startswith("q1_")]
    assert "orders.sales_channel" not in saved.cube.dimensions and "dealers.region" in saved.cube.dimensions
    fake_llm.push(routed(query={"metrics": ["order_count"]}), {**WORDS, "paragraphs": ["All: {fact:q3.order_count}."]})
    answer("And volume?", report.id, ctx)
    after = ctx.reports.get(report.id)
    assert [s.id for s in after.spec.sections] == ["s2", "s3"] and "q3" in after.query_defs
    assert valid(after) == []


def test_remove_last_section_is_refused(ctx, report):
    with pytest.raises(ValueError, match="A report needs at least one section."):
        remove_section(report, "s1", ctx)


def test_can_edit_rules(report):
    assert can_edit("ana", report)
    assert not can_edit("omar", report)
    assert can_edit("dev", report.model_copy(update={"author": "dev"}))


# Task 7: edit wording in place; reorder and remove blocks.
from living_reports.harness.commands import (  # noqa: E402
    SpecRejected,
    edit_prose,
    remove_block,
    reorder_blocks,
)
from living_reports.reports.editions import EditionStore  # noqa: E402


def test_edit_prose_keeps_tokens_and_marks_contributor(ctx, report):
    editions = EditionStore(ctx.store, ctx.reports)
    before = editions.draft_hash(report)
    saved = edit_prose(report, "s1", ["Total {fact:q1_total.fraud_loss}.", "  "], ctx, "dev")
    assert prose_of(saved) == ["Total {fact:q1_total.fraud_loss}."]
    assert "dev" in saved.contributors
    assert valid(saved) == []
    assert editions.draft_hash(saved) != before
    blocks = saved.spec.sections[0].blocks
    assert isinstance(blocks[0], Prose)


def test_edit_prose_rejects_raw_numbers_and_empty(ctx, report):
    with pytest.raises(SpecRejected) as error:
        edit_prose(report, "s1", ["Loss rose 38%."], ctx, "ana")
    assert [i.code for i in error.value.issues] == ["raw_number"]
    with pytest.raises(SpecRejected) as error:
        edit_prose(report, "s1", ["", "   "], ctx, "ana")
    assert [(i.code, i.message) for i in error.value.issues] == [("empty", "A section needs at least one paragraph.")]
    with pytest.raises(SpecRejected) as error:
        edit_prose(report, "s1", ["See {fact:q9.nope} and <b>this</b>."], ctx, "ana")
    assert {i.code for i in error.value.issues} == {"unknown_fact", "markup"}
    assert ctx.reports.get(report.id).version == report.version


def test_reorder_and_remove_blocks(ctx, report):
    n = len(report.spec.sections[0].blocks)
    saved = reorder_blocks(report, "s1", list(reversed(range(n))), ctx)
    blocks = saved.spec.sections[0].blocks
    assert isinstance(blocks[0], Bars) and isinstance(blocks[-1], Prose)
    with pytest.raises(ValueError, match="order must list every block once"):
        reorder_blocks(saved, "s1", [0] * n, ctx)
    saved = remove_block(saved, "s1", 0, ctx)
    assert not [b for b in saved.spec.sections[0].blocks if isinstance(b, Bars)]
    assert valid(saved) == []
    prose_at = next(i for i, b in enumerate(saved.spec.sections[0].blocks) if isinstance(b, Prose))
    with pytest.raises(ValueError, match="Edit the words to remove a paragraph."):
        remove_block(saved, "s1", prose_at, ctx)
    with pytest.raises(ValueError, match="No block 99"):
        remove_block(saved, "s1", 99, ctx)


def test_can_edit_never_comes_from_contributors(report):
    assert not can_edit("omar", report.model_copy(update={"contributors": ["ana", "omar"]}))
    assert can_edit("dev", report.model_copy(update={"author": "dev"}))


# --- claims in the section commands ---------------------------------------------------------

CITED = {**WORDS, "paragraphs": ["In short, {claim:q1.value.fraud_loss}."]}


@pytest.fixture
def cited(ctx, fake_llm):
    fake_llm.push(routed(query=LOSS_BY_CHANNEL), CITED)
    return ctx.reports.get(answer("Where is the loss?", None, ctx).report_id)


def test_a_rewrite_reads_the_brief_and_may_cite_claims(ctx, fake_llm, cited):
    fake_llm.push({"paragraphs": ["{claim:q1.value.fraud_loss}."]})
    saved = rewrite_section(cited, "s1", "shorter", ctx)
    assert prose_of(saved) == ["{claim:q1.value.fraud_loss}."]
    assert "Claims (each one is checked against the data):" in fake_llm.prompts[-1]
    assert valid(saved) == []


def test_a_rewrite_with_a_claim_word_is_repaired(ctx, fake_llm, cited):
    fake_llm.push({"paragraphs": ["Loss is rising."]}, {"paragraphs": ["{claim:q1.value.fraud_loss}."]})
    saved = rewrite_section(cited, "s1", "regenerate", ctx)
    assert prose_of(saved) == ["{claim:q1.value.fraud_loss}."]
    assert "claim_word" in fake_llm.prompts[-1]


def test_a_rerun_marks_a_cited_claim_that_no_longer_holds(ctx, fake_llm, cited):
    query = Query(**LOSS_BY_CHANNEL, filters=[east()])
    saved, dropped, _ = rerun_section(cited, "s1", query, ctx)
    section = saved.spec.sections[0]
    assert not dropped and "q1.value.fraud_loss" in section.stale_claims
    # The stale claim keeps its words, so the page can show what is no longer true.
    assert saved.spec.claims["q1.value.fraud_loss"].text == cited.spec.claims["q1.value.fraud_loss"].text
    assert valid(saved) == []


def test_a_rerun_with_the_same_query_keeps_claims_true(ctx, fake_llm, cited):
    saved, _, _ = rerun_section(cited, "s1", Query(**LOSS_BY_CHANNEL), ctx)
    assert saved.spec.sections[0].stale_claims == []
    assert all(f in saved.facts for c in saved.spec.claims.values() for f in c.facts)


def test_a_person_may_keep_claim_tokens(ctx, fake_llm, cited):
    saved = edit_prose(cited, "s1", ["Here: {claim:q1.value.fraud_loss}."], ctx, "ana")
    assert prose_of(saved) == ["Here: {claim:q1.value.fraud_loss}."]


def test_removing_a_section_removes_its_claims(ctx, fake_llm, cited):
    fake_llm.push(routed(query={"metrics": ["order_count"], "dimensions": ["dealers.region"]}),
                  {**WORDS, "paragraphs": ["{claim:q2.value.order_count}."]})
    answer("Orders by region?", cited.id, ctx)
    saved = remove_section(ctx.reports.get(cited.id), "s1", ctx)
    assert {c.query_ref for c in saved.spec.claims.values()} == {"q2"}


def test_removing_a_section_keeps_claims_that_another_section_cites(ctx, fake_llm, cited):
    fake_llm.push(routed(query={"metrics": ["order_count"], "dimensions": ["dealers.region"]}),
                  {**WORDS, "paragraphs": ["As before, {claim:q1.value.fraud_loss}."]})
    answer("Orders by region?", cited.id, ctx)
    saved = remove_section(ctx.reports.get(cited.id), "s1", ctx)
    assert "q1.value.fraud_loss" in saved.spec.claims
    assert valid(saved) == []
