import { useState, type ReactNode } from "react";

type Props = { title: string; tools?: ReactNode; table: ReactNode; children: ReactNode; footer?: ReactNode };

/** A chart with its title, its tools, and a switch to the same data as a table. */
export function ChartCard({ title, tools, table, children, footer }: Props) {
  const [asTable, setAsTable] = useState(false);
  return (
    <section className="card">
      <div className="card-head">
        <h3 className="card-title">{title}</h3>
        <div className="tools">
          {tools}
          <button type="button" aria-pressed={asTable} onClick={() => setAsTable(!asTable)}>Table</button>
        </div>
      </div>
      {asTable ? table : children}
      {footer}
    </section>
  );
}
