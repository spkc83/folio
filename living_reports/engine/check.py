"""The read-only check (spec 5.2.6). It gates compiled queries and exploratory SQL.

A statement passes when it is one ``SELECT`` (or a set operation of selects), every table
it reads in each scope is a CTE or derived table visible there or a dataset of the model,
it calls no table or file function, it projects no ``*``, and every column resolves to a
declared field. ``check_select`` returns the qualified AST: the caller runs the SQL that
this AST renders, never the text it received.
"""

import sqlglot
from sqlglot import exp
from sqlglot.errors import OptimizeError, SqlglotError
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import traverse_scope

from living_reports.engine.errors import QueryRejected
from living_reports.semantic.model import SemanticModel

_ONE_SELECT = "Only one SELECT statement is allowed."


def parse_select(sql: str, dialect: str) -> exp.Expression:
    try:
        statements = [s for s in sqlglot.parse(sql, read=dialect) if s is not None]
    except SqlglotError as error:
        raise QueryRejected(str(error)) from error
    if len(statements) != 1 or not isinstance(statements[0], exp.Select | exp.SetOperation):
        raise QueryRejected(_ONE_SELECT)
    return statements[0]


def _split(table: str) -> tuple[str, str]:
    db, _, name = table.rpartition(".")
    return (db or "main"), name


def _not_a_dataset(table: exp.Table) -> QueryRejected:
    return QueryRejected(f"{table.sql()} is not a dataset of the model.")


def _check_functions(ast: exp.Expression) -> None:
    """No function that sqlglot does not know, and no reader of files (read_csv, ...)."""
    for node in ast.walk():
        if isinstance(node, exp.Anonymous):
            raise QueryRejected(f"The function {node.name} is not allowed.")
        if isinstance(node, exp.Func) and type(node).__name__.startswith("Read"):
            raise QueryRejected(f"The function {node.sql()} is not allowed.")


def _check_tables(ast: exp.Expression, model: SemanticModel) -> None:
    """Resolve each table in its own scope: a visible CTE or derived table, or a dataset."""
    for table in ast.find_all(exp.Table):
        if not isinstance(table.this, exp.Identifier) or table.args.get("catalog"):
            raise _not_a_dataset(table)
    declared = {_split(d.table) for d in model.datasets}
    try:
        scopes = traverse_scope(ast)
    except Exception as error:  # sqlglot raises several types on shapes it cannot walk
        raise QueryRejected(f"The statement cannot be checked: {error}") from error
    for scope in scopes:
        for table in scope.tables:
            source = scope.sources.get(table.alias_or_name)
            if not table.db and table.name in scope.cte_sources and source is not table:
                continue  # a CTE visible in this scope
            if ((table.db or "main"), table.name) not in declared:
                raise _not_a_dataset(table)
    # traverse_scope skips a scope it cannot walk (it logs and goes on), so every table in
    # the tree must also be a dataset or a CTE that the statement defines.
    ctes = {cte.alias_or_name for cte in ast.find_all(exp.CTE)}
    for table in ast.find_all(exp.Table):
        if not table.db and table.name in ctes:
            continue
        if ((table.db or "main"), table.name) not in declared:
            raise _not_a_dataset(table)
    if any(s.args.get("into") for s in ast.find_all(exp.Select)):
        raise QueryRejected(_ONE_SELECT)


def _check_shapes(ast: exp.Expression) -> None:
    """Refuse shapes that can read a column the model does not declare."""
    for node in ast.walk():
        if isinstance(node, exp.Pivot):
            raise QueryRejected("PIVOT and UNPIVOT are not allowed.")
        if isinstance(node, exp.PositionalColumn):
            raise QueryRejected("A positional column (#1) is not allowed; name each column.")
        if isinstance(node, exp.TableAlias) and node.columns and isinstance(node.parent, exp.Table):
            raise QueryRejected(
                f"A column list on the table {node.parent.name} is not allowed; "
                "use the field names."
            )


def _check_projections(ast: exp.Expression) -> None:
    """Each output column is named: no ``*``, ``t.*``, or ``COLUMNS(...)`` projection."""
    for node in ast.walk():
        if isinstance(node, exp.Columns):
            raise QueryRejected("COLUMNS(...) is not allowed; name each column.")
        if isinstance(node, exp.Star) and not isinstance(node.parent, exp.Count):
            raise QueryRejected("A * projection is not allowed; name each column.")


def _check_resolved(qualified: exp.Expression) -> None:
    """After qualify, a column with no table must be an output alias outside the select list.

    DuckDB reads a bare table alias in the select list as the whole row, so a projected
    name that resolves to no field is refused.
    """
    for row in qualified.find_all(exp.TableColumn):
        raise QueryRejected(f"{row.name} is a table, not a field; name each column.")
    for column in qualified.find_all(exp.Column):
        if column.table:
            continue
        select = column.find_ancestor(exp.Select)
        projected = select is not None and any(
            column is p or column in p.find_all(exp.Column) for p in select.expressions
        )
        aliases = set(select.named_selects) if select is not None else set()
        if projected or column.name not in aliases:
            raise QueryRejected(f"{column.name} is not a field of a dataset in the model.")


def check_select(ast: exp.Expression, model: SemanticModel, dialect: str) -> exp.Expression:
    """Raise ``QueryRejected`` unless ``ast`` reads only the model's datasets and fields.

    Returns the qualified AST, which is the statement to run.
    """
    if not isinstance(ast, exp.Select | exp.SetOperation):
        raise QueryRejected(_ONE_SELECT)
    _check_functions(ast)
    _check_shapes(ast)
    _check_tables(ast, model)
    _check_projections(ast)
    # compile imports this module, so its helper is imported when a check needs it.
    from living_reports.engine.compile import sqlglot_schema

    try:
        qualified = qualify(
            ast.copy(),
            schema=sqlglot_schema(model),
            dialect=dialect,
            db="main",
            validate_qualify_columns=True,
        )
    except (OptimizeError, SqlglotError) as error:
        raise QueryRejected(str(error)) from error
    except Exception as error:  # qualify asserts on shapes it does not support
        raise QueryRejected(f"The statement cannot be checked: {error}") from error
    _check_resolved(qualified)
    return qualified
