import pytest

from living_reports.store import get_store
from living_reports.semantic.demo import seed_demo
from living_reports.sandbox.gateway import SandboxContext
from living_reports.sandbox.runner import deno_path, run_code
from living_reports.sandbox.tools import TOOLS, declarations

pytestmark = pytest.mark.skipif(deno_path() is None, reason="deno is not installed")


@pytest.fixture
def ctx(store2):
    store = get_store()
    seed_demo(store.graph)
    return SandboxContext(store=store, source_id="duckdb_demo")


def wrap_try(code):
    return f"try {{ const r = {code}; console.log('ESCAPED', String(r)); }} catch (e) {{ console.log(e.name); }}"


@pytest.mark.parametrize("code", [
    "await Deno.readTextFile('/etc/passwd')",
    "await fetch('https://example.com')",
    "Deno.env.get('GOOGLE_API_KEY')",
    "new Deno.Command('sh').outputSync()",
    "await Deno.writeTextFile('/tmp/lr-escape', 'x')",
    "Deno.dlopen('/lib64/libc.so.6', {})",
    "await Deno.connect({hostname: '127.0.0.1', port: 8100})",
])
def test_escape_attempts_fail(code, ctx):
    r = run_code(wrap_try(code), ctx)
    assert "ESCAPED" not in r.stdout
    assert "NotCapable" in r.stdout or "PermissionDenied" in r.stdout or r.killed_reason


def test_console_output_is_returned(ctx):
    assert run_code("console.log('hello', 1 + 1)", ctx).stdout.strip() == "hello 2"


def test_error_in_code_is_reported(ctx):
    r = run_code("throw new Error('boom')", ctx)
    assert "boom" in r.error


def test_infinite_loop_is_killed(ctx):
    r = run_code("while (true) {}", ctx, timeout_s=1)
    assert r.killed_reason == "timeout"


def test_memory_bomb_is_killed(ctx):
    code = "const a = []; while (true) { a.push(new Uint8Array(50_000_000).fill(1)); }"
    r = run_code(code, ctx, timeout_s=8, memory_mb=128)
    assert r.killed_reason in ("memory", "oom")


def test_heap_bomb_is_killed(ctx):
    code = "const a = []; while (true) { a.push(new Array(1000).fill('x'.repeat(100))); }"
    assert run_code(code, ctx, timeout_s=8, memory_mb=64).killed_reason in ("memory", "oom")


def test_output_is_capped(ctx):
    r = run_code("for (let i = 0; i < 100000; i++) console.log('x'.repeat(100));", ctx)
    assert len(r.stdout) <= 70_000


def test_tool_call_returns_summary_not_all_rows(ctx):
    code = """
    const r = await tools.metrics_query({metrics: ['order_count'], time: {grain: 'month'}});
    console.log(r.row_count, r.rows.length, r.facts.length);
    """
    r = run_code(code, ctx)
    count, shown, facts = r.stdout.split()
    assert int(count) == 25 and int(shown) == 20 and int(facts) <= 20


def test_tool_results_are_recorded_for_the_report(ctx):
    r = run_code("await tools.metrics_query({metrics: ['order_count']});", ctx)
    assert r.tool_calls == 1 and len(r.queries) == 1


def test_chained_calls_work(ctx):
    code = """
    const a = await tools.metrics_query({metrics: ['fraud_loss'], dimensions: ['orders.sales_channel'], limit: 1});
    const top = a.rows[0][0];
    const b = await tools.metrics_query({metrics: ['order_count'], filters: [{member: 'orders.sales_channel', op: 'eq', values: [top]}]});
    console.log(top, b.rows[0][0]);
    """
    r = run_code(code, ctx)
    assert r.tool_calls == 2 and len(r.stdout.split()) == 2 and r.error is None


def test_unknown_tool_is_rejected(ctx):
    r = run_code("try { await tools.drop_everything({}); } catch (e) { console.log(e.message); }", ctx)
    assert "not a function" in r.stdout or "unknown" in r.stdout.lower()


def test_forged_protocol_message_cannot_reach_a_hidden_tool(ctx):
    code = """
    const enc = new TextEncoder();
    Deno.stdout.writeSync(enc.encode(JSON.stringify({type: 'call', id: 999, tool: 'shell', args: {}}) + '\\n'));
    console.log('done');
    """
    r = run_code(code, ctx)
    assert r.tool_calls == 0


def test_bad_arguments_return_an_error_to_the_code(ctx):
    code = "try { await tools.metrics_query({metrics: []}); } catch (e) { console.log('caught'); }"
    assert "caught" in run_code(code, ctx).stdout


def test_out_of_scope_query_is_an_error_not_a_crash(ctx):
    code = "try { await tools.metrics_query({metrics: ['profit']}); } catch (e) { console.log(e.message); }"
    assert "profit" in run_code(code, ctx).stdout


def test_graph_lookup_returns_a_definition(ctx):
    r = run_code("console.log((await tools.graph_lookup({id: 'fraud_rate'})).definition);", ctx)
    assert "Share of orders" in r.stdout


def test_add_section_records_words(ctx):
    r = run_code("await tools.report_add_section({heading: 'H', paragraphs: ['P']});", ctx)
    assert r.sections == [{"heading": "H", "paragraphs": ["P"]}]


def test_tools_are_mcp_tools_and_declarations_are_typescript():
    assert {t.name for t in TOOLS} == {"metrics_query", "graph_lookup", "report_add_section"}
    text = declarations(TOOLS)
    assert "metrics_query(args:" in text and "declare const tools" in text


def test_no_credentials_in_the_sandbox_environment(ctx, monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "secret-value")
    r = run_code("console.log('ran');", ctx)
    assert "secret-value" not in r.stdout and "ran" in r.stdout


def test_declarations_describe_the_new_query():
    text = declarations(TOOLS)
    assert "member: string" in text and "grain?:" in text
    assert "filters?: (Filter | FilterGroup)[]" in text and "time?: TimeRange | null" in text


def test_graph_lookup_reads_a_dimension_from_the_vocabulary(ctx):
    r = run_code("const t = await tools.graph_lookup({id: 'dealers.region'}); "
                 "console.log(t.kind, t.label, t.dataset);", ctx)
    assert r.stdout.split() == ["dimension", "Region", "dealers"]


def test_a_nested_filter_group_runs_in_code_mode(ctx):
    code = """
    const r = await tools.metrics_query({metrics: ['order_count'], filters: [{or: [
      {member: 'orders.sales_channel', op: 'eq', values: ['App']},
      {member: 'dealers.region', op: 'eq', values: ['East']}]}]});
    console.log(r.rows[0][0]);
    """
    assert int(run_code(code, ctx).stdout.strip()) > 0
