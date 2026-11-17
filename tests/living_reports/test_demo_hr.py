"""The HR demo: the generator and its planted stories."""
import duckdb
import pytest

from living_reports.demos import get_demo

DEMO = get_demo("hr")
J = "employee_months JOIN employees USING (employee_id)"


@pytest.fixture(scope="module")
def folder(tmp_path_factory):
    out = tmp_path_factory.mktemp("hr")
    DEMO.generate(out)
    return out


def q(folder, sql):
    con = duckdb.connect()
    for t in ("employee_months", "employees"):
        con.execute(f"CREATE VIEW {t} AS SELECT * FROM read_parquet('{folder / (t + '.parquet')}')")
    return con.execute(sql).fetchall()


def one(folder, sql):
    return q(folder, sql)[0][0]


def test_the_tables_have_their_rows_and_unique_keys(folder):
    assert 250_000 <= one(folder, "SELECT count(*) FROM employee_months") <= 350_000
    for table, key in DEMO.keys.items():
        rows, distinct = q(folder, f"SELECT count(*), count(DISTINCT {key[0]}) FROM {table}")[0]
        assert rows == distinct, table
    # One row per employee and month, and nothing after the month an employee leaves.
    assert one(folder, "SELECT count(*) - count(DISTINCT (employee_id, month)) FROM employee_months") == 0
    assert one(folder, "SELECT max(n) FROM (SELECT employee_id, sum(is_leaver) n FROM employee_months GROUP BY 1)") == 1
    assert one(folder, "SELECT count(*) FROM employee_months e JOIN (SELECT employee_id, month AS left_in "
                       "FROM employee_months WHERE is_leaver = 1) l USING (employee_id) WHERE e.month > l.left_in") == 0


def test_the_flags_are_never_null(folder):
    # A NULL leaver flag made avg(is_leaver) count only the leavers' rows: 9.5%, not 1.2%.
    nulls = "SELECT count(*) FILTER (is_leaver IS NULL OR is_voluntary IS NULL OR is_new_hire IS NULL) FROM employee_months"
    assert one(folder, nulls) == 0


def test_no_protected_attributes(folder):
    columns = {c[0] for t in ("employee_months", "employees")
               for c in q(folder, f"DESCRIBE {t}")}
    assert not columns & {"gender", "sex", "ethnicity", "race", "age", "birth_date", "religion"}


def test_the_generator_gives_the_same_data_every_time(folder, tmp_path):
    DEMO.generate(tmp_path)
    # An exact digest: a float sum can differ in its last bits with the order of a parallel scan.
    digest = "SELECT count(*), sum(hash(e)::HUGEINT) FROM employee_months e"
    assert q(folder, digest) == q(tmp_path, digest)


def test_moment_1_lacks_december_and_the_engagement_score(folder):
    con = duckdb.connect()
    sql = DEMO.tables(folder, 1)["employee_months"]
    assert con.execute(f"SELECT max(month) FROM ({sql})").fetchone()[0].month == 11
    assert "engagement_score" not in [c[0] for c in con.execute(f"DESCRIBE {sql}").fetchall()]


RATE = "SELECT avg(is_leaver) FROM " + J + " WHERE {}"


def test_engineering_junior_attrition_triples_after_the_march_2025_reorganization(folder):
    group = "department = 'Engineering' AND job_level = 'Junior'"
    before = one(folder, RATE.format(group + " AND month < DATE '2025-04-01'"))
    after = one(folder, RATE.format(group + " AND month >= DATE '2025-04-01'"))
    assert 2.3 <= after / before <= 4.0


def test_customer_support_absence_is_highest_and_rises_through_2025(folder):
    by_dept = dict(q(folder, "SELECT department, avg(absence_rate) FROM " + J + " GROUP BY 1"))
    assert max(by_dept, key=by_dept.get) == "Customer support"
    rows = q(folder, "SELECT quarter(month), avg(absence_rate), avg(overtime_hours) FROM " + J +
                     " WHERE department = 'Customer support' AND year(month) = 2025 GROUP BY 1 ORDER BY 1")
    absence = [a for _, a, _ in rows]
    overtime = [o for _, _, o in rows]
    assert all(a < b for a, b in zip(absence, absence[1:]))
    assert all(a < b for a, b in zip(overtime, overtime[1:]))


def test_manchester_misses_the_attrition_target_and_remote_meets_it(folder):
    rates = dict(q(folder, "SELECT location, avg(is_leaver) FROM " + J + " GROUP BY 1"))
    assert rates["Manchester"] > 0.012 and rates["Remote"] < 0.012


def test_a_hiring_freeze_in_the_second_half_of_2025(folder):
    h1 = one(folder, "SELECT sum(is_new_hire) FROM employee_months WHERE month >= DATE '2025-01-01' AND month < DATE '2025-07-01'")
    h2 = one(folder, "SELECT sum(is_new_hire) FROM employee_months WHERE month >= DATE '2025-07-01'")
    assert 0.2 <= h2 / h1 <= 0.45


def test_people_under_6_months_leave_at_about_3_times_the_rate(folder):
    rates = dict(q(folder, "SELECT tenure_band = 'Under 6 months', avg(is_leaver) FROM employee_months GROUP BY 1"))
    assert 2.3 <= rates[True] / rates[False] <= 4.0


def test_a_new_leave_reason_starts_in_december_2025(folder):
    first = one(folder, "SELECT min(month) FROM employee_months WHERE leave_reason = 'Redundancy'")
    assert (first.year, first.month) == (2025, 12)


def test_a_question_about_people_who_left_names_the_leavers():
    from living_reports.demos.hr import METRICS
    from living_reports.harness.decision_route import mentions_metric
    from living_reports.semantic.model import Term
    terms = [Term(id=m.name, label=m.label, synonyms=m.synonyms) for m in METRICS]
    assert mentions_metric("How many people left from the London office?", terms)
    assert not mentions_metric("What is the people number?", terms)
