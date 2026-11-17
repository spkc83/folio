"""The HR demo: about 15,000 employees as about 300,000 employee-months, 2024 to 2025.

One row per employee and month while the person is employed. The leaving month is the
person's last row. No protected attributes (gender, ethnicity, age) are in the data.

Planted stories (the tests in ``test_demo_hr.py`` hold each one to a range):

1. Engineering Junior attrition about triples after a reorganization in March 2025.
2. Customer support has the highest absence rate, and its absence and overtime rise in every
   quarter of 2025.
3. Manchester misses the 1.2% monthly attrition target; Remote meets it.
4. A hiring freeze: new hires fall by about 70% in the second half of 2025.
5. People under 6 months of tenure leave at about 3 times the rate of others.
6. A new leave reason, Redundancy, starts in December 2025 with a restructuring.
"""
from pathlib import Path

import duckdb

from living_reports.semantic.kinds import FilterSpec, Measure, MetricBindingPayload

from . import Definition, Demo, moment_tables, u, write_parquet
from ._model import D, F, R, seed_model

STAFF = 11_000  # employed on 1 January 2024
HIRES = 4_600  # hired in 2024 and 2025


def generate(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute("""
    CREATE TABLE months AS
    SELECT m::DATE AS month, sum(w) OVER (ORDER BY m) / sum(w) OVER () AS c
    FROM (SELECT m, CASE WHEN m >= TIMESTAMP '2025-07-01' THEN 0.3 ELSE 1.0 END AS w
          FROM range(TIMESTAMP '2024-01-01', TIMESTAMP '2026-01-01', INTERVAL 1 MONTH) t(m))""")
    # Staff hired before 2024 (spread over 12 years), then the hires of 2024 and 2025.
    con.execute(f"""
    CREATE TABLE hired AS
    SELECT i, DATE '2012-01-01' + floor({u('i', 'hired')} * 4380)::INT AS hire_date
    FROM range({STAFF}) t(i)
    UNION ALL
    SELECT b.i, m.month + floor({u('b.i', 'hire_day')} * 28)::INT
    FROM (SELECT i, {u('i', 'hire_month')} AS u_month FROM range({STAFF}, {STAFF + HIRES}) t(i)) b
    ASOF JOIN months m ON m.c >= b.u_month""")
    con.execute(f"""
    CREATE TABLE employees AS
    SELECT 'E' || lpad((i + 1)::VARCHAR, 5, '0') AS employee_id,
           CASE WHEN d < 0.25 THEN 'Engineering' WHEN d < 0.45 THEN 'Sales'
                WHEN d < 0.67 THEN 'Customer support' WHEN d < 0.82 THEN 'Operations'
                WHEN d < 0.90 THEN 'Finance' ELSE 'People' END AS department,
           CASE WHEN l < 0.35 THEN 'London' WHEN l < 0.60 THEN 'Manchester'
                WHEN l < 0.75 THEN 'Edinburgh' ELSE 'Remote' END AS location,
           CASE WHEN j < 0.35 THEN 'Junior' WHEN j < 0.70 THEN 'Mid'
                WHEN j < 0.90 THEN 'Senior' ELSE 'Lead' END AS job_level,
           hire_date
    FROM (SELECT i, hire_date, {u('i', 'dept')} AS d, {u('i', 'location')} AS l,
                 {u('i', 'level')} AS j FROM hired)""")
    con.execute(f"""
    CREATE TABLE candidates AS
    WITH x AS (
      SELECT e.*, m.month, date_diff('month', e.hire_date, m.month) AS tenure,
             {u("e.employee_id || m.month::VARCHAR", 'leave')} AS u_leave
      FROM employees e JOIN months m ON m.month >= date_trunc('month', e.hire_date)
    )
    SELECT *,
      u_leave < 0.009
        * CASE WHEN tenure < 6 THEN 3.0 ELSE 1.0 END
        * CASE WHEN department = 'Engineering' AND job_level = 'Junior'
                    AND month >= DATE '2025-04-01' THEN 3.0 ELSE 1.0 END
        * CASE location WHEN 'Manchester' THEN 1.45 WHEN 'Remote' THEN 0.7 ELSE 1.0 END AS hit
    FROM x""")
    con.execute(f"""
    CREATE TABLE employee_months AS
    WITH left_in AS (
      SELECT employee_id, min(month) AS leave_month FROM candidates WHERE hit GROUP BY 1
    ), kept AS (
      SELECT c.*, l.leave_month,
             {u("c.employee_id || c.month::VARCHAR", 'reason')} AS u_reason,
             {u("c.employee_id || c.month::VARCHAR", 'absence')} AS u_absence,
             {u("c.employee_id || c.month::VARCHAR", 'overtime')} AS u_overtime,
             {u("c.employee_id || c.month::VARCHAR", 'salary')} AS u_salary,
             {u("c.employee_id || c.month::VARCHAR", 'rating')} AS u_rating,
             {u("c.employee_id || c.month::VARCHAR", 'engagement')} AS u_engagement
      FROM candidates c LEFT JOIN left_in l USING (employee_id)
      WHERE l.leave_month IS NULL OR c.month <= l.leave_month
    ), shaped AS (
      SELECT *,
        coalesce(month = leave_month, false) AS leaver,
        CASE WHEN department = 'Customer support'
             THEN CASE WHEN year(month) = 2025 THEN [1.0, 1.15, 1.3, 1.5][quarter(month)] ELSE 0.9 END
             ELSE 0.55 END AS absence_mean,
        CASE WHEN department = 'Customer support'
             THEN CASE WHEN year(month) = 2025 THEN [12, 14, 16, 18][quarter(month)] ELSE 10 END
             ELSE 6 END AS overtime_mean
      FROM kept
    )
    SELECT
      employee_id || '-' || strftime(month, '%Y%m') AS record_id,
      month::TIMESTAMP AS month,
      employee_id,
      CASE WHEN tenure < 6 THEN 'Under 6 months' WHEN tenure < 12 THEN '6-12 months'
           WHEN tenure < 36 THEN '1-3 years' ELSE '3+ years' END AS tenure_band,
      leaver::INT AS is_leaver,
      (leaver AND u_reason < 0.75)::INT AS is_voluntary,
      CASE WHEN NOT leaver THEN 'None' WHEN u_reason < 0.75 THEN 'Resignation'
           WHEN month >= DATE '2025-12-01' AND u_reason < 0.93 THEN 'Redundancy'
           WHEN u_reason < 0.83 THEN 'Retirement' WHEN u_reason < 0.93 THEN 'Dismissal'
           ELSE 'End of contract' END AS leave_reason,
      (date_trunc('month', hire_date) = month)::INT AS is_new_hire,
      floor(u_absence * 2 * absence_mean + 0.5)::INT AS absence_days,
      round(floor(u_absence * 2 * absence_mean + 0.5) / 21.0, 4) AS absence_rate,
      round(overtime_mean * (0.5 + u_overtime), 1) AS overtime_hours,
      round(CASE job_level WHEN 'Junior' THEN 32000 WHEN 'Mid' THEN 48000 WHEN 'Senior' THEN 68000
                           ELSE 90000 END
            * CASE department WHEN 'Engineering' THEN 1.25 WHEN 'Finance' THEN 1.1
                              WHEN 'Customer support' THEN 0.85 WHEN 'Operations' THEN 0.9
                              WHEN 'People' THEN 0.95 ELSE 1.0 END
            * (0.9 + 0.2 * u_salary) * CASE WHEN year(month) = 2025 THEN 1.03 ELSE 1.0 END, -2)
        AS salary,
      CASE WHEN u_rating < 0.05 THEN 1 WHEN u_rating < 0.20 THEN 2 WHEN u_rating < 0.70 THEN 3
           WHEN u_rating < 0.92 THEN 4 ELSE 5 END AS performance_rating,
      round(greatest(1.0, least(5.0,
        CASE WHEN department = 'Engineering' AND job_level = 'Junior' AND month >= DATE '2025-04-01'
             THEN 2.6 WHEN department = 'Customer support' THEN 3.0 ELSE 3.6 END
        + (u_engagement - 0.5) * 1.6)), 1) AS engagement_score
    FROM shaped
    ORDER BY month, employee_id""")
    write_parquet(con, folder, {"employee_months": "*", "employees": "*"})


def _tables(folder: Path, moment: int) -> dict[str, str]:
    return moment_tables(folder, moment, "employee_months", "month", "engagement_score",
                         ["employees"])


DATASETS = {
    "employee_months": D("record_id", {
        "record_id": F("string"),
        "month": F("datetime", "Month", time=True),
        "employee_id": F("string"),
        "tenure_band": F("string", "Tenure band", synonyms=["tenure"]),
        "leave_reason": F("string", "Leave reason"),
        "is_leaver": F("integer"),
        "is_voluntary": F("integer"),
        "is_new_hire": F("integer"),
        "absence_days": F("integer"),
        "absence_rate": F("decimal"),
        "overtime_hours": F("decimal"),
        "salary": F("decimal"),
        "performance_rating": F("integer"),
    }, time_field="month"),
    "employees": D("employee_id", {
        "employee_id": F("string"),
        "department": F("string", "Department", synonyms=["team"]),
        "location": F("string", "Location", synonyms=["office", "site"]),
        "job_level": F("string", "Job level", synonyms=["level", "grade"]),
    }),
}


def _m(name, label, fmt, definition, better=None, target=None, *, measure, synonyms=()):
    return MetricBindingPayload(name=name, label=label, format=fmt, definition=definition,
                                measure=measure, better=better, target=target,
                                synonyms=list(synonyms))


METRICS = [
    _m("employee_count", "Employees", "count", "Number of people employed at any time in the period.",
       measure=Measure(agg="count_distinct", field="employee_months.employee_id"),
       synonyms=["headcount", "staff"]),
    _m("employee_month_count", "Employee-months", "count", "Months of employment, one for each person and month.",
       measure=Measure(agg="count", dataset="employee_months")),
    _m("attrition_rate", "Attrition rate", "percent", "Share of employees who leave in a month.",
       "lower", 0.012, measure=Measure(agg="avg", field="employee_months.is_leaver"),
       synonyms=["turnover", "attrition"]),
    _m("voluntary_attrition_rate", "Voluntary attrition rate", "percent",
       "Share of employees who resign in a month.", "lower",
       measure=Measure(agg="avg", field="employee_months.is_voluntary")),
    _m("leavers", "Leavers", "count", "Number of people who left.", "lower",
       measure=Measure(agg="sum", field="employee_months.is_leaver"), synonyms=["left", "leaving"]),
    _m("new_hires", "New hires", "count", "Number of people who joined.",
       measure=Measure(agg="sum", field="employee_months.is_new_hire"), synonyms=["hires", "hiring"]),
    _m("absence_rate", "Absence rate", "percent", "Share of working days lost to absence.",
       "lower", 0.03, measure=Measure(agg="avg", field="employee_months.absence_rate"),
       synonyms=["absence", "sickness"]),
    _m("average_overtime", "Average overtime hours", "number", "Overtime hours of an average employee in a month.",
       "lower", measure=Measure(agg="avg", field="employee_months.overtime_hours"),
       synonyms=["overtime"]),
    _m("average_salary", "Average salary", "currency", "Annual salary of an average employee.",
       measure=Measure(agg="avg", field="employee_months.salary"), synonyms=["pay"]),
]


def seed(fg, *, source_id: str = "duckdb_demo", dialect: str = "duckdb",
         schema: str = "main") -> None:
    seed_model(fg, DATASETS, [R("employee_months", "employees", "employee_id")], METRICS,
               source_id=source_id, dialect=dialect, schema=schema)


DEMO = Demo(
    name="hr",
    title="People and HR",
    placeholder="Where is attrition highest?",
    keys={"employee_months": ("record_id",), "employees": ("employee_id",)},
    seed=seed,
    tables=_tables,
    generate=generate,
    definition=Definition(
        metric="leavers",
        filter=FilterSpec(member="employee_months.leave_reason", op="ne", values=["Retirement"]),
        definition="Number of people who left, excluding retirements.",
        title="Leavers: exclude retirements",
        reason="Retirements are planned exits and move to the workforce plan.",
    ),
    questions=Path(__file__).parent.parent / "evals" / "questions_hr.yaml",
    starters=[
        "What is the attrition rate by department?",
        "How has the absence rate moved this year?",
        "Which locations miss the attrition target?",
    ],
)
