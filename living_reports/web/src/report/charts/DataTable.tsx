import { useState, type ReactNode } from "react";
import { columnLabel, type QueryView } from "../../lib/api";
import { formatOf, formatValue } from "./format";

const MAX_ROWS = 50;
const text = (v: unknown) => (v === null || v === undefined ? "No data" : String(v));
const member = (v: unknown) => (v === null || v === undefined ? "No value" : String(v));

/** Query rows as a table. A header click sorts by that column, then reverses. */
type Props = {
  query: QueryView; title: string;
  /** Cross-filter: a click on a dimension cell selects that member. */
  onSelect?: (dimension: string, value: string | null) => void;
  dimensions?: string[];
  footer?: ReactNode;
};

export function DataTable({ query, title, onSelect, dimensions, footer }: Props) {
  const [sort, setSort] = useState<{ col: number; dir: 1 | -1 } | null>(null);
  const numeric = query.columns.map(
    (_, j) => query.rows.length > 0 && query.rows.every((r) => r[j] === null || typeof r[j] === "number"),
  );
  const rows = [...query.rows];
  if (sort) {
    rows.sort((a, b) => {
      const [va, vb] = [a[sort.col], b[sort.col]];
      if (va === vb) return 0;
      if (va === null || va === undefined) return 1;
      if (vb === null || vb === undefined) return -1;
      return ((va as number | string) < (vb as number | string) ? -1 : 1) * sort.dir;
    });
  }
  const dims = dimensions ?? query.columns.filter((_, j) => !numeric[j]);
  // The column that the points carry shows in the points' format ("$18.9M", not 18887914.85).
  const points = query.points ?? [];
  const pointColumn = points.length === query.rows.length && points.length > 0
    ? query.columns.findIndex((_, j) => numeric[j] && query.rows.every((r, i) => r[j] === points[i].value))
    : -1;
  const pointFormat = pointColumn >= 0 ? formatOf(points[0].formatted) : null;
  const show = (v: unknown, j: number) => (j === pointColumn && pointFormat && typeof v === "number" ? formatValue(v, pointFormat) : text(v));
  const raw = (v: unknown) => (v === null || v === undefined ? null : String(v));
  // A number selects the member of its row (the first dimension that is not the month).
  const rowDim = query.columns.findIndex((c) => dims.includes(c) && c !== "month");
  const cell = (v: unknown, j: number, row: unknown[]) => {
    const column = query.columns[j];
    if (onSelect && dims.includes(column) && column !== "month") {
      return <button type="button" className="sort" onClick={() => onSelect(column, raw(v))}>{member(v)}</button>;
    }
    if (onSelect && numeric[j] && rowDim >= 0) {
      return <button type="button" className="sort" onClick={() => onSelect(query.columns[rowDim], raw(row[rowDim]))}>{show(v, j)}</button>;
    }
    return show(v, j);
  };
  const next = (j: number) => (sort?.col === j && sort.dir === 1 ? { col: j, dir: -1 as const } : { col: j, dir: 1 as const });
  return (
    <section className="card">
      <h3 className="card-title">{title}</h3>
      {query.rows.length === 0 && <p className="empty">No data for this selection.</p>}
      {query.rows.length > 0 && (
        <div className="table-scroll">
          <table className="data">
            <thead>
              <tr>
                {query.columns.map((c, j) => (
                  <th key={c} className={numeric[j] ? "num" : undefined}
                    aria-sort={sort?.col === j ? (sort.dir === 1 ? "ascending" : "descending") : undefined}>
                    <button type="button" className="sort" onClick={() => setSort(next(j))}>{columnLabel(query, c)}</button>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.slice(0, MAX_ROWS).map((row, i) => (
                <tr key={i}>{row.map((v, j) => <td key={j} className={numeric[j] ? "num" : undefined}>{cell(v, j, row)}</td>)}</tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {query.rows.length > MAX_ROWS && <p className="meta">Showing {MAX_ROWS} of {query.rows.length} rows.</p>}
      {footer}
    </section>
  );
}
