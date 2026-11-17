"""Compile a ``Query`` into one SQL statement over the ``SemanticModel`` (spec 5.2).

``compile`` is a pure function of the query and the model. It never reads the graph or a
database. Identifiers come only from the model. Every value from the query is a
``Placeholder``: the SQL text never holds a filter value, and the placeholder style
(``?`` or ``%s``) comes from sqlglot's dialect.
"""

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass

import sqlglot
from pydantic import BaseModel
from sqlglot import exp

from living_reports.engine.check import check_select
from living_reports.engine.errors import OutOfScope, QueryRejected
from living_reports.engine.expressions import render
from living_reports.engine.paths import Join, join_path
from living_reports.engine.query import Filter, FilterGroup, Having, Query
from living_reports.semantic.kinds import FilterSpec, Measure
from living_reports.semantic.model import Metric, SemanticModel

_SQL_TYPES = {
    "string": "TEXT",
    "integer": "BIGINT",
    "decimal": "DOUBLE",
    "boolean": "BOOLEAN",
    "datetime": "TIMESTAMP",
    "date": "DATE",
}
_AGGS = {"sum": exp.Sum, "avg": exp.Avg, "min": exp.Min, "max": exp.Max}
_COMPARE = {"eq": exp.EQ, "ne": exp.NEQ, "gt": exp.GT, "gte": exp.GTE, "lt": exp.LT, "lte": exp.LTE}

Value = Callable[[object], exp.Expression]


class Plan(BaseModel):
    base_datasets: list[str]
    joins: dict[str, list[str]]
    group_columns: list[str]
    time_column: str | None = None
    time_field: str | None = None
    metric_versions: dict[str, int]
    member_versions: dict[str, int]
    dialect: str
    model_version: int


@dataclass(frozen=True)
class Compiled:
    sql: str
    params: list
    plan: Plan
    hash: str
    ast: exp.Expression


def _split_table(table: str) -> tuple[str, str]:
    db, _, name = table.rpartition(".")
    return (db or "main"), name


def sqlglot_schema(model: SemanticModel) -> dict:
    """``{db: {table: {column: type}}}`` for every dataset. Computed fields are not columns."""
    schema: dict[str, dict[str, dict[str, str]]] = {}
    for ds in model.datasets:
        db, name = _split_table(ds.table)
        columns = {f.column: _SQL_TYPES[f.type] for f in ds.fields if f.expression is None}
        schema.setdefault(db, {})[name] = columns
    return schema


# Expressions ---------------------------------------------------------------------------


def field_expr(model: SemanticModel, member: str) -> exp.Expression:
    """The row-level expression of a member, on its dataset alias."""
    f = model.field(member)
    if f.expression is None:
        return exp.column(f.column, table=f.dataset)
    tree = sqlglot.parse_one(f.expression.sql, read=f.expression.dialect)
    for col in tree.find_all(exp.Column):
        col.set("table", exp.to_identifier(f.dataset))
    return tree if isinstance(tree, exp.Column | exp.Literal) else exp.Paren(this=tree)


def _arg(model: SemanticModel, member: str) -> exp.Expression:
    e = field_expr(model, member)
    return e.this if isinstance(e, exp.Paren) else e


def condition(
    flt: FilterSpec, target: exp.Expression, value: Value
) -> exp.Expression:
    """One filter as a boolean expression. ``value`` turns a filter value into a node."""
    vals = flt.values
    if flt.op in _COMPARE:
        return _COMPARE[flt.op](this=target, expression=value(vals[0]))
    if flt.op in ("in", "not_in"):
        node = exp.In(this=target, expressions=[value(v) for v in vals])
        return node if flt.op == "in" else exp.Not(this=node)
    if flt.op == "between":
        return exp.Between(this=target, low=value(vals[0]), high=value(vals[1]))
    if flt.op == "is_null":
        return exp.Is(this=target, expression=exp.Null())
    if flt.op == "contains":
        # The value is text to find, so its own % and _ are escaped, not wildcards.
        text = str(vals[0]).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = exp.Like(this=target, expression=value(f"%{text}%"))
        return exp.Escape(this=like, expression=exp.Literal.string("\\"))
    raise OutOfScope(f"The filter operator {flt.op} is not supported")


def aggregate(model: SemanticModel, m: Measure, value: Value) -> exp.Expression:
    """The aggregate expression of a typed measure."""
    if m.agg == "count":
        agg = exp.Count(this=exp.Star() if m.field is None else _arg(model, m.field))
    elif m.agg == "count_distinct":
        agg = exp.Count(this=exp.Distinct(expressions=[_arg(model, m.field)]))
    else:
        agg = _AGGS[m.agg](this=_arg(model, m.field))
    if m.filter is not None:
        cond = condition(m.filter, field_expr(model, m.filter.member), value)
        agg = exp.Filter(this=agg, expression=exp.Where(this=cond))
    return agg


def _literal(v: object) -> exp.Expression:
    if isinstance(v, bool):
        return exp.Boolean(this=v)
    if isinstance(v, int | float):
        return exp.Literal.number(v)
    return exp.Literal.string(str(v))


def measure_sql(measure: Measure, model: SemanticModel, dialect: str) -> str:
    """A typed measure as SQL text, with its filter values as literals (a definition, not a query)."""
    return aggregate(model, measure, _literal).sql(dialect=dialect)


def _placeholder(v: object) -> exp.Expression:
    node = exp.Placeholder()
    node.meta["value"] = v
    return node


def _ratio(num: exp.Expression, den: exp.Expression) -> exp.Expression:
    return exp.Div(
        this=exp.Cast(this=num, to=exp.DataType.build("DOUBLE")),
        expression=exp.Nullif(this=den, expression=exp.Literal.number(0)),
    )


def _coalesce(parts: list[exp.Expression]) -> exp.Expression:
    return parts[0] if len(parts) == 1 else exp.Coalesce(this=parts[0], expressions=parts[1:])


def _table(table: str, alias: str) -> exp.Expression:
    return exp.alias_(exp.to_table(table), alias, table=True)


def _alias(name: str) -> exp.Column:
    return exp.column(exp.to_identifier(name, quoted=True))


# Compilation ---------------------------------------------------------------------------


class _Compiler:
    def __init__(self, query: Query, model: SemanticModel, dialect: str):
        self.q, self.model, self.dialect = query, model, dialect
        self._resolve()

    # Resolution

    def _filters(self, items: list[Filter | FilterGroup]) -> list[FilterSpec]:
        out: list[FilterSpec] = []
        for item in items:
            out += self._filters(item.items) if isinstance(item, FilterGroup) else [item]
        return out

    def _resolve(self) -> None:
        model, q = self.model, self.q
        metric_ids = set(model.metric_versions)
        members = model.members
        unknown: list[str] = []
        self.metric_versions: dict[str, int] = {}

        def visit(mid: str, path: tuple[str, ...]) -> None:
            if mid in path:
                raise OutOfScope(f"{mid} refers to itself through {' / '.join(path)}", [mid])
            if mid not in metric_ids:
                unknown.append(mid)
                return
            metric = model.metric(mid)
            self.metric_versions[mid] = metric.version
            if metric.ratio is not None:
                visit(metric.ratio.numerator, (*path, mid))
                visit(metric.ratio.denominator, (*path, mid))

        for mid in q.metrics:
            visit(mid, ())
        flat = self._filters(q.filters)
        used = [*q.dimensions, *(f.member for f in flat)]
        if q.time is not None and q.time.field is not None:
            used.append(q.time.field)
        unknown += [m for m in used if m not in members]
        if unknown:
            unknown = list(dict.fromkeys(unknown))
            raise OutOfScope(f"The model has no entry for: {', '.join(unknown)}", unknown)
        self.flat_filters = flat
        self.leaves: list[str] = []
        for mid in q.metrics:
            self._collect_leaves(mid)
        self.bases = list(dict.fromkeys(self.base_of(m) for m in self.leaves))
        self.time_field = self._time_field()

    def _collect_leaves(self, mid: str) -> None:
        metric = self.model.metric(mid)
        if metric.ratio is not None:
            self._collect_leaves(metric.ratio.numerator)
            self._collect_leaves(metric.ratio.denominator)
        elif mid not in self.leaves:
            self.leaves.append(mid)

    def base_of(self, mid: str) -> str:
        metric = self.model.metric(mid)
        if metric.measure is not None:
            m = metric.measure
            return self.model.field(m.field).dataset if m.field else m.dataset
        if metric.expression is not None:
            return metric.expression.dataset
        raise OutOfScope(f"{mid} has no base dataset")

    def _time_field(self) -> str | None:
        if self.q.time is None:
            return None
        if self.q.time.field is not None:
            return self.q.time.field
        base = self.model.dataset(self.bases[0])
        if base.time_field is None:
            raise OutOfScope(f"{base.name} has no time field", [])
        return f"{base.name}.{base.time_field}"

    # Members a base needs

    def _measure_members(self, mid: str) -> list[str]:
        metric = self.model.metric(mid)
        if metric.measure is not None:
            m = metric.measure
            return [x for x in (m.field, m.filter.member if m.filter else None) if x]
        if metric.expression is not None:
            return list(metric.expression.fields)
        return []

    def _joins(self, base: str, members: list[str]) -> list[Join]:
        joins: dict[str, Join] = {}
        datasets = sorted({self.model.field(m).dataset for m in members} - {base})
        if self.time_field is not None:
            dataset = self.model.field(self.time_field).dataset
            if dataset != base and not self._reaches(base, dataset):
                raise OutOfScope(f"{base} has no time field", [])
        for target in datasets:
            for j in self._path(base, target, members):
                seen = joins.get(j.to_dataset)
                if seen is not None and seen.relationship.name != j.relationship.name:
                    raise OutOfScope(
                        f"{j.to_dataset} is reached through {seen.relationship.name} and "
                        f"{j.relationship.name}; the query is ambiguous",
                        [],
                    )
                joins[j.to_dataset] = j
        return list(joins.values())

    def _path(self, base: str, target: str, members: list[str]) -> list[Join]:
        """The join path, or a refusal that names the metric and the member in words."""
        try:
            return join_path(self.model, base, target)
        except OutOfScope as error:
            if not error.reason.startswith("No join path"):
                raise
            metric = next(
                self.model.metric(m) for m in self.q.metrics if base in self._bases_of(m)
            )
            member = next(m for m in members if self.model.field(m).dataset == target)
            raise OutOfScope(
                f"{metric.label} cannot be broken down or filtered by "
                f"{self.model.field(member).label}: {base} has no link to {target}",
                error.unknown,
                detail=error.reason,
            ) from error

    def _bases_of(self, mid: str) -> set[str]:
        metric = self.model.metric(mid)
        if metric.ratio is not None:
            return self._bases_of(metric.ratio.numerator) | self._bases_of(metric.ratio.denominator)
        return {self.base_of(mid)}

    def _reaches(self, base: str, dataset: str) -> bool:
        try:
            join_path(self.model, base, dataset)
        except OutOfScope:
            return False
        return True

    def members_of(self, base: str, leaves: list[str]) -> list[str]:
        members = [*self.q.dimensions, *(f.member for f in self.flat_filters)]
        if self.time_field is not None:
            members.append(self.time_field)
        for mid in leaves:
            members += self._measure_members(mid)
        return list(dict.fromkeys(members))

    # SQL pieces

    def _column(self, m: str) -> exp.Expression:
        return field_expr(self.model, m)

    def where(self, items: list[Filter | FilterGroup]) -> exp.Expression | None:
        parts = []
        for item in items:
            if isinstance(item, FilterGroup):
                inner = [self.where([x]) for x in item.items]
                joined = exp.and_(*inner) if item.all is not None else exp.or_(*inner)
                parts.append(exp.Paren(this=joined) if len(inner) > 1 else joined)
            else:
                parts.append(condition(item, self._column(item.member), _placeholder))
        return exp.and_(*parts) if parts else None

    def time_conditions(self) -> list[exp.Expression]:
        t = self.q.time
        if t is None:
            return []
        col = self._column(self.time_field)
        out = []
        if t.start is not None:
            out.append(exp.GTE(this=col.copy(), expression=_placeholder(t.start)))
        if t.end is not None:
            out.append(exp.LT(this=col.copy(), expression=_placeholder(t.end)))
        return out

    def leaf_expr(self, mid: str) -> exp.Expression:
        metric = self.model.metric(mid)
        if metric.expression is not None:
            try:
                return render(metric.expression, self.model, lambda dataset: dataset)
            except QueryRejected as error:
                raise OutOfScope(f"{mid}: {error.reason}", [mid]) from error
        return aggregate(self.model, metric.measure, _placeholder)

    def metric_expr(self, mid: str, leaf: Callable[[str], exp.Expression]) -> exp.Expression:
        metric: Metric = self.model.metric(mid)
        if metric.ratio is not None:
            return _ratio(
                self.metric_expr(metric.ratio.numerator, leaf),
                self.metric_expr(metric.ratio.denominator, leaf),
            )
        return leaf(mid)

    def from_with_joins(self, base: str, joins: list[Join]) -> exp.Select:
        ds = self.model.dataset(base)
        select = exp.select().from_(_table(ds.table, base))
        for j in joins:
            to = self.model.dataset(j.to_dataset)
            on = exp.and_(
                *(
                    exp.EQ(
                        this=exp.column(self._physical(j.from_dataset, a), table=j.from_dataset),
                        expression=exp.column(self._physical(j.to_dataset, b), table=j.to_dataset),
                    )
                    for a, b in zip(j.relationship.from_columns, j.relationship.to_columns)
                )
            )
            select = select.join(_table(to.table, j.to_dataset), on=on, join_type="left")
        return select

    def _physical(self, dataset: str, name: str) -> str:
        f = self.model.members.get(f"{dataset}.{name}")
        return f.column if f is not None else name

    def having_condition(self, h: Having, target: exp.Expression) -> exp.Expression:
        spec = FilterSpec.model_construct(member="x.x", op=h.op, values=h.values)
        return condition(spec, target, _placeholder)

    # The statement

    @property
    def time_column(self) -> str | None:
        if self.q.time is None or self.q.time.grain is None:
            return None
        return f"{self.time_field}:{self.q.time.grain}"

    def group_columns(self) -> list[tuple[str, exp.Expression]]:
        """(alias, expression) per group key: the dimensions, then the time column."""
        cols = [(d, self._column(d)) for d in self.q.dimensions]
        if self.time_column is not None:
            unit = exp.Literal.string(self.q.time.grain.upper())
            cols.append((self.time_column, exp.DateTrunc(unit=unit, this=self._column(self.time_field))))
        return cols

    def order(self, select: exp.Select, group_aliases: list[str]) -> exp.Select:
        q = self.q
        if q.order:
            keys = [(self._order_key(k), d == "desc") for k, d in q.order]
        elif self.time_column is not None:
            keys = [(self.time_column, False)]
        else:
            keys = [(q.metrics[0], True)]
        named = {k for k, _ in keys}
        keys += [(g, False) for g in group_aliases if g not in named]
        ordered = [exp.Ordered(this=_alias(k), desc=desc, nulls_first=False) for k, desc in keys]
        return select.order_by(*ordered)

    def _order_key(self, key: str) -> str:
        """An order key ``<field>:<grain>`` written before the time field was resolved."""
        grain = self.q.time.grain if self.q.time else None
        if grain is not None and key.endswith(f":{grain}") and key not in self.q.dimensions:
            return self.time_column
        return key

    def _conditions(self) -> list[exp.Expression]:
        conds = []
        w = self.where(self.q.filters)
        if w is not None:
            conds.append(w)
        return conds + self.time_conditions()

    def _satellite(
        self, base: str, leaves: list[str], keyed: bool
    ) -> tuple[exp.Select, list[Join]]:
        """One base: its joins, filters, time range, and GROUP BY over the group keys.

        ``keyed`` selects the group keys as ``k0..kn`` and each leaf metric under its id,
        for the outer query of a multi-base plan.
        """
        joins = self._joins(base, self.members_of(base, leaves))
        select = self.from_with_joins(base, joins)
        group = self.group_columns()
        names = [f"k{i}" for i in range(len(group))] if keyed else [a for a, _ in group]
        select = select.select(*(e.copy().as_(n, quoted=True) for n, (_, e) in zip(names, group)))
        metrics = leaves if keyed else self.q.metrics
        select = select.select(
            *(self.metric_expr(m, self.leaf_expr).as_(m, quoted=True) for m in metrics)
        )
        conds = self._conditions()
        if conds:
            select = select.where(exp.and_(*conds))
        if group:
            select = select.group_by(*(e.copy() for _, e in group))
        return select, joins

    def _having(self, metric_of: Callable[[str], exp.Expression]) -> exp.Expression | None:
        if not self.q.having:
            return None
        return exp.and_(
            *(self.having_condition(h, metric_of(h.metric)) for h in self.q.having)
        )

    def build(self) -> tuple[exp.Select, list[str], dict[str, list[str]]]:
        aliases = [a for a, _ in self.group_columns()]
        if len(self.bases) == 1:
            base = self.bases[0]
            select, joins = self._satellite(base, self.leaves, keyed=False)
            having = self._having(lambda m: self.metric_expr(m, self.leaf_expr))
            if having is not None:
                select = select.having(having)
            plan_joins = {base: [j.relationship.name for j in joins]}
        else:
            select, plan_joins = self._lowered(aliases)
        select = self.order(select, aliases)
        if self.q.limit:
            select = select.limit(self.q.limit)
        return select, aliases, plan_joins

    def _lowered(self, aliases: list[str]) -> tuple[exp.Select, dict[str, list[str]]]:
        """Spine and satellites (spec 5.2.3): each base pre-aggregates to the group keys."""
        keys = [f"k{i}" for i in range(len(aliases))]
        names = [f"s{i}" for i in range(len(self.bases))]
        plan_joins: dict[str, list[str]] = {}
        sources: dict[str, str] = {}
        outer: exp.Select | None = None
        for i, base in enumerate(self.bases):
            leaves = [m for m in self.leaves if self.base_of(m) == base]
            sat, joins = self._satellite(base, leaves, keyed=True)
            plan_joins[base] = [j.relationship.name for j in joins]
            sources.update({m: names[i] for m in leaves})
            sub = exp.alias_(exp.Subquery(this=sat), names[i], table=True)
            if outer is None:
                outer = exp.select().from_(sub)
                continue
            if not keys:
                outer = outer.join(sub, join_type="cross")
                continue
            on = exp.and_(
                *(
                    exp.NullSafeEQ(
                        this=_coalesce([exp.column(k, table=n) for n in names[:i]]),
                        expression=exp.column(k, table=names[i]),
                    )
                    for k in keys
                )
            )
            outer = outer.join(sub, on=on, join_type="full outer")

        def leaf(m: str) -> exp.Expression:
            return exp.column(exp.to_identifier(m, quoted=True), table=sources[m])

        outer = outer.select(
            *(
                _coalesce([exp.column(k, table=n) for n in names]).as_(a, quoted=True)
                for k, a in zip(keys, aliases)
            )
        )
        outer = outer.select(*(self.metric_expr(m, leaf).as_(m, quoted=True) for m in self.q.metrics))
        having = self._having(lambda m: self.metric_expr(m, leaf))
        if having is not None:
            outer = outer.where(having)
        return outer, plan_joins

    def member_versions(self) -> dict[str, int]:
        members = self.model.members
        used: list[str] = []
        for base in self.bases:
            used += self.members_of(base, [m for m in self.leaves if self.base_of(m) == base])
        return {m: members[m].version for m in dict.fromkeys(used)}

    def row_count(self) -> tuple[exp.Select, dict[str, list[str]]]:
        base = self.bases[0]
        members = [f.member for f in self.flat_filters]
        if self.time_field is not None:
            members.append(self.time_field)
        joins = self._joins(base, members)
        select = self.from_with_joins(base, joins).select(exp.Count(this=exp.Star()))
        conds = self._conditions()
        if conds:
            select = select.where(exp.and_(*conds))
        return select, {base: [j.relationship.name for j in joins]}


def _finish(
    ast: exp.Expression, plan: Plan, model: SemanticModel, dialect: str
) -> Compiled:
    check_select(ast, model, dialect)
    sql = ast.sql(dialect=dialect)
    placeholders = [n for n in ast.dfs() if isinstance(n, exp.Placeholder)]
    params = [n.meta["value"] for n in placeholders if "value" in n.meta]
    assert len(params) == len(placeholders), "each placeholder must carry its value"
    hashed = plan.model_dump(mode="json", exclude={"model_version"})
    digest = json.dumps([sql, [str(p) for p in params], hashed], sort_keys=True)
    return Compiled(
        sql=sql,
        params=params,
        plan=plan,
        hash=hashlib.sha256(digest.encode()).hexdigest(),
        ast=ast,
    )


def compile(query: Query, model: SemanticModel, dialect: str | None = None) -> Compiled:
    """One SQL statement for ``query``. Raises ``OutOfScope`` with a sentence."""
    dialect = dialect or model.dialect
    c = _Compiler(query, model, dialect)
    ast, group_columns, joins = c.build()
    plan = Plan(
        base_datasets=c.bases,
        joins=joins,
        group_columns=group_columns,
        time_column=c.time_column,
        time_field=c.time_field,
        metric_versions=c.metric_versions,
        member_versions=c.member_versions(),
        dialect=dialect,
        model_version=model.model_version,
    )
    return _finish(ast, plan, model, dialect)


def row_count_query(query: Query, model: SemanticModel, dialect: str | None = None) -> Compiled:
    """``SELECT COUNT(*)`` over the first base dataset with the query's filters and time range."""
    dialect = dialect or model.dialect
    c = _Compiler(query, model, dialect)
    ast, joins = c.row_count()
    plan = Plan(
        base_datasets=c.bases[:1],
        joins=joins,
        group_columns=[],
        time_field=c.time_field,
        metric_versions={},
        member_versions=c.member_versions(),
        dialect=dialect,
        model_version=model.model_version,
    )
    return _finish(ast, plan, model, dialect)
