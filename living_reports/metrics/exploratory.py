"""Read-only exploratory SQL. These answers are never certified."""
import hashlib

from living_reports.engine.check import check_select, parse_select
from living_reports.engine.errors import QueryRejected
from living_reports.metrics.runner import Result
from living_reports.semantic.model import SemanticModel
from living_reports.sources import (
    SourceError,
    columns_of,
    data_time_of,
    normalize,
    rows_of,
    truncated,
)
from living_reports.sources.registry import source_for

MAX_ROWS = 1000
TIMEOUT_S = 20.0


class SqlRejected(Exception):
    pass


def _final_sql(sql: str, model: SemanticModel, dialect: str) -> str:
    """The SQL to run: rendered from the checked, qualified AST, never the text received.

    The source parses the rendered text again and runs it only when it is one SELECT.
    """
    try:
        qualified = check_select(parse_select(sql, dialect), model, dialect)
    except QueryRejected as error:
        raise SqlRejected(error.reason) from error
    return qualified.sql(dialect=dialect, comments=False)


def run_exploratory(sql: str, source_id: str, model: SemanticModel) -> Result:
    """Run one read-only SELECT that reads only the model's datasets and fields."""
    try:
        source = source_for(source_id)
        sql = _final_sql(sql, model, source.dialect)
        table = normalize(source.run(sql, limit=MAX_ROWS, timeout_s=TIMEOUT_S))
        data_time = data_time_of(table) or source.data_time()
    except SourceError as error:
        raise SqlRejected(str(error)) from error
    rows = rows_of(table)
    return Result(
        columns=columns_of(table),
        rows=rows,
        query_hash=hashlib.sha256(sql.encode()).hexdigest(),
        source_id=source_id,
        data_time=data_time,
        row_count=len(rows),
        sql=sql,
        params=[],
        metric_versions={},
        metric_meta={},
        dimensions=[],
        truncated=truncated(table),
    )
