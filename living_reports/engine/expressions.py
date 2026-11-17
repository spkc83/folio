"""Expression metrics (spec 11.3): one aggregate over one dataset, stored as a sqlglot AST.

The text names fields by member id (``orders.loss_amount``), never by physical column.
The rules are mechanical, so a check can confirm them:

- The root is an aggregate, or an aggregate with a ``FILTER (WHERE ...)``.
- Every node is of a listed class: listed aggregates, arithmetic, comparisons, boolean
  logic, CASE and IF, casts to listed types, COALESCE and NULLIF, date parts, literals,
  and columns. Any other node (read_csv, getenv, a window, ...) is refused.
- No other aggregate and no subquery.
- Every column is ``dataset.field`` for a field of the metric's dataset.
"""

from collections.abc import Callable

import sqlglot
from sqlglot import exp, serde
from sqlglot.errors import SqlglotError

from living_reports.engine.errors import QueryRejected
from living_reports.semantic.kinds import Expression
from living_reports.semantic.model import SemanticModel

DIALECTS = ("duckdb", "postgres")

# The only sqlglot node classes an expression metric may hold. Anything else is refused,
# so a new or a dialect-specific function (read_csv, getenv, ...) never reaches SQL.
_AGGREGATES = (
    exp.Sum, exp.Count, exp.Avg, exp.Min, exp.Max, exp.Median,
    exp.PercentileCont, exp.PercentileDisc, exp.Stddev, exp.StddevPop, exp.StddevSamp,
    exp.Variance, exp.VariancePop,
)
_ALLOWED = (
    *_AGGREGATES,
    exp.Filter, exp.Where,
    exp.Add, exp.Sub, exp.Mul, exp.Div, exp.Mod, exp.Neg, exp.Paren, exp.Abs, exp.Round,
    exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE, exp.And, exp.Or, exp.Not, exp.Is,
    exp.In, exp.Between, exp.Like, exp.ILike,
    exp.Case, exp.If, exp.Cast, exp.TryCast, exp.DataType, exp.Coalesce, exp.Nullif,
    exp.DateDiff, exp.DateTrunc, exp.TimestampTrunc, exp.Extract,
    exp.Literal, exp.Null, exp.Boolean, exp.Column, exp.Identifier,
)
_CAST_TYPES = {
    exp.DataType.Type.DOUBLE, exp.DataType.Type.FLOAT, exp.DataType.Type.DECIMAL,
    exp.DataType.Type.INT, exp.DataType.Type.BIGINT, exp.DataType.Type.SMALLINT,
    exp.DataType.Type.BOOLEAN, exp.DataType.Type.DATE, exp.DataType.Type.TIMESTAMP,
    exp.DataType.Type.VARCHAR, exp.DataType.Type.TEXT,
}
_DATE_PARTS = {"day", "week", "month", "quarter", "year", "hour", "minute", "second"}
_DATE_FUNCS = (exp.DateDiff, exp.DateTrunc, exp.TimestampTrunc, exp.Extract)


def _node_name(node: exp.Expression) -> str:
    return node.name if isinstance(node, exp.Anonymous) and node.name else type(node).__name__


def _check_node(node: exp.Expression) -> None:
    """Refuse a node whose class is not on the list, or that sits where it cannot."""
    parent = node.parent
    if isinstance(node, exp.Star | exp.Distinct):
        if isinstance(parent, exp.Count) and parent.this is node:
            return
    elif isinstance(node, exp.Var):
        if isinstance(parent, _DATE_FUNCS) and node.name.lower() in _DATE_PARTS:
            return
    elif isinstance(node, exp.Literal) and isinstance(parent, _DATE_FUNCS):
        if not node.is_string or node.name.lower() in _DATE_PARTS:
            return
    elif isinstance(node, exp.DataType):
        if node.this in _CAST_TYPES and not node.expressions:
            return
    elif isinstance(node, _ALLOWED):
        return
    raise QueryRejected(f"{_node_name(node)} is not allowed in an expression metric.")


def _check_dialect(dialect: str) -> None:
    if dialect not in DIALECTS:
        raise QueryRejected(
            f"The dialect {dialect} is not supported; use one of: {', '.join(DIALECTS)}."
        )


def _parse(sql: str, dialect: str) -> exp.Expression:
    try:
        statements = sqlglot.parse(sql, read=dialect)
    except SqlglotError as error:
        raise QueryRejected(f"The expression does not parse: {error}") from error
    if len(statements) != 1 or statements[0] is None:
        raise QueryRejected("An expression metric is one aggregate expression, not statements.")
    return statements[0]


def _strip(tree: exp.Expression) -> exp.Expression:
    """Remove parse positions, so the stored AST depends only on the expression."""
    for node in tree.walk():
        node._meta = None
    return tree


def _check_shape(tree: exp.Expression, dataset: str) -> list[str]:
    """Apply the root, aggregate, subquery, function, and column rules. Return member ids."""
    agg = tree.this if isinstance(tree, exp.Filter) else tree
    if not isinstance(agg, exp.AggFunc):
        raise QueryRejected(
            "An expression metric must be one aggregate, for example sum(orders.loss_amount)."
        )
    if any(isinstance(n, exp.Subquery | exp.Select) for n in tree.walk()):
        raise QueryRejected("An expression metric cannot contain a subquery.")
    inner = [n for n in tree.walk() if isinstance(n, exp.AggFunc) and n is not agg]
    if inner:
        raise QueryRejected(
            f"An expression metric has one aggregate; {inner[0].sql()} is a second one."
        )
    for node in tree.walk():
        _check_node(node)
    fields = []
    for col in tree.find_all(exp.Column):
        text = col.sql()
        if col.table != dataset or col.args.get("db") or col.args.get("catalog"):
            raise QueryRejected(f"{text} is not a field of {dataset}; use the member id")
        fields.append(f"{dataset}.{col.name}")
    return sorted(set(fields))


def _check_members(fields: list[str], dataset: str, model: SemanticModel) -> None:
    members = model.members
    for member in fields:
        if member not in members or members[member].dataset != dataset:
            raise QueryRejected(f"{member} is not a field of {dataset}; use the member id")


def parse_expression(
    sql: str, dataset: str, model: SemanticModel, dialect: str = "duckdb"
) -> Expression:
    """Parse and check an expression metric's text. Raises ``QueryRejected`` with a sentence."""
    _check_dialect(dialect)
    if dataset not in {d.name for d in model.datasets}:
        raise QueryRejected(f"The model has no dataset {dataset}.")
    tree = _strip(_parse(sql, dialect))
    fields = _check_shape(tree, dataset)
    _check_members(fields, dataset, model)
    return Expression(sql=sql, dialect=dialect, dataset=dataset, fields=fields, ast=serde.dump(tree))


def load(payload: Expression) -> exp.Expression:
    try:
        tree = serde.load(payload.ast)
    except Exception as error:  # serde raises several types on a malformed dump
        raise QueryRejected("The expression AST is not a valid sqlglot tree.") from error
    if not isinstance(tree, exp.Expression):
        raise QueryRejected("The expression AST is not a valid sqlglot tree.")
    return tree


def validate_expression(payload: Expression, model: SemanticModel | None = None) -> None:
    """The stored AST loads, matches its text, follows the rules, and lists its fields."""
    _check_dialect(payload.dialect)
    tree = load(payload)
    fields = _check_shape(tree, payload.dataset)
    if sorted(payload.fields) != fields:
        raise QueryRejected(
            f"The expression fields {payload.fields} do not match its AST, which uses {fields}."
        )
    text = _strip(_parse(payload.sql, payload.dialect))
    if text != tree:
        raise QueryRejected("The expression text does not match its stored AST.")
    if model is not None:
        _check_members(fields, payload.dataset, model)


def render(
    expression: Expression, model: SemanticModel, alias_of: Callable[[str], str]
) -> exp.Expression:
    """The stored AST with each member replaced by its physical column on ``alias_of(dataset)``."""
    # compile imports this module, so its helper is imported when a render needs it.
    from living_reports.engine.compile import field_expr

    validate_expression(expression, model)
    tree = load(expression)
    alias = alias_of(expression.dataset)

    def swap(node: exp.Expression) -> exp.Expression:
        if not isinstance(node, exp.Column):
            return node
        target = field_expr(model, f"{expression.dataset}.{node.name}")
        for col in target.find_all(exp.Column):
            col.set("table", exp.to_identifier(alias))
        parent = node.parent
        if isinstance(target, exp.Paren) and isinstance(parent, exp.Func):
            return target.this
        return target

    return tree.transform(swap)
