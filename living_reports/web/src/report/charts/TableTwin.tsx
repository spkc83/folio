/** The same values as the chart, as a table. Every chart has one. */
export function TableTwin({ columns, rows, numeric = [] }: { columns: string[]; rows: string[][]; numeric?: number[] }) {
  const isNum = (j: number) => numeric.includes(j);
  return (
    <div className="table-scroll">
      <table className="data">
        <thead>
          <tr>{columns.map((c, j) => <th key={j} className={isNum(j) ? "num" : undefined}>{c}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i}>{row.map((v, j) => <td key={j} className={isNum(j) ? "num" : undefined}>{v}</td>)}</tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
