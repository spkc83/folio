import type { ReactNode } from "react";
import type { Block, Spec } from "../../lib/api";
import { Chart, isChart } from "../Chart";
import { Tile } from "../Tile";

export function Dashboard({ spec, panel }: { spec: Spec; panel: ReactNode }) {
  const blocks = spec.sections.flatMap((s) => s.blocks);
  const tiles = blocks.filter((b): b is Extract<Block, { type: "tile" }> => b.type === "tile");
  const charts = blocks.filter(isChart);
  const isWide = (c: (typeof charts)[number]) => c.type === "line" || c.type === "small_multiples";
  const compact = charts.filter((c) => !isWide(c));
  const wide = charts.filter(isWide);
  return (
    <>
      <div className="kicker">{spec.kicker}</div>
      <h1 className="title">{spec.title}</h1>
      <div className="tiles">
        {tiles.map((t) => <Tile key={t.fact_id} block={t} />)}
      </div>
      {panel}
      <div className="grid2">{compact.map((c) => <Chart key={c.query_ref} block={c} />)}</div>
      {wide.map((c) => <Chart key={c.query_ref} block={c} />)}
    </>
  );
}
