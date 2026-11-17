import { useState } from "react";
import { members, months } from "./cube";
import { monthLabel, nextMonth, NO_VALUE, withMember, withoutMember, withRange, type Scope } from "./scope";
import { useScope } from "./ScopeContext";

const NULL_TOKEN = "\u0000null";
const encode = (v: string | null) => (v === null ? NULL_TOKEN : v);
const decode = (v: string) => (v === NULL_TOKEN ? null : v);

type Preset = "all" | "12" | "6" | "custom";

export const EXPLORE_OFF = "Month-by-month exploration is not available for this report.";

function presetOf(scope: Scope, last: string | undefined, custom: boolean): Preset {
  if (custom) return "custom";
  if (!scope.range) return "all";
  if (last && scope.range[1] === last && scope.range[0] === nextMonth(last, -11)) return "12";
  if (last && scope.range[1] === last && scope.range[0] === nextMonth(last, -5)) return "6";
  return "custom";
}

/** One row of filters above the report. A change re-aggregates the cube; no model call. */
export function FilterRow() {
  const { scope, setScope, cube, reason } = useScope();
  const [custom, setCustom] = useState(false);
  if (!cube) {
    // The reason is technical ("A query has a date bound that is not on day 1…"): a tooltip.
    return reason ? <p className="filters meta" title={reason}>{EXPLORE_OFF}</p> : null;
  }
  const all = months(cube);
  const first = all[0], last = all[all.length - 1];
  // Reset clears the range, and with it a custom choice.
  const preset = presetOf(scope, last, custom && scope.range !== null);

  const onDates = (value: Preset) => {
    setCustom(value === "custom");
    if (value === "all") setScope({ ...scope, range: null });
    if (value === "12" && last) setScope(withRange(scope, nextMonth(last, -11), last));
    if (value === "6" && last) setScope(withRange(scope, nextMonth(last, -5), last));
    if (value === "custom" && first && last && !scope.range) setScope(withRange(scope, first, last));
  };
  const onMember = (dimension: string, value: string) => {
    const cleared = withoutMember(scope, dimension);
    setScope(value === "" ? cleared : withMember(cleared, dimension, decode(value)));
  };
  const [from, to] = scope.range ?? [first, last];

  return (
    <div className="filters" role="group" aria-label="Report filters">
      <label className="filter">
        <span>Dates</span>
        <select aria-label="Dates" value={preset} onChange={(e) => onDates(e.target.value as Preset)}>
          <option value="all">All dates</option>
          <option value="12">Last 12 months</option>
          <option value="6">Last 6 months</option>
          <option value="custom">Custom…</option>
        </select>
      </label>
      {preset === "custom" && (
        <>
          <label className="filter">
            <span>From</span>
            <select aria-label="From" value={from} onChange={(e) => setScope(withRange(scope, e.target.value, to))}>
              {all.map((m) => <option key={m} value={m}>{monthLabel(m)}</option>)}
            </select>
          </label>
          <label className="filter">
            <span>To</span>
            <select aria-label="To" value={to} onChange={(e) => setScope(withRange(scope, from, e.target.value))}>
              {all.map((m) => <option key={m} value={m}>{monthLabel(m)}</option>)}
            </select>
          </label>
        </>
      )}
      {cube.dimensions.filter((d) => d !== "month").map((dim) => {
        const label = cube.labels[dim] ?? dim;
        const current = scope.filters.find((f) => f.dimension === dim);
        return (
          <label className="filter" key={dim}>
            <span>{label}</span>
            <select aria-label={label} value={current ? encode(current.value) : ""}
              onChange={(e) => onMember(dim, e.target.value)}>
              <option value="">All</option>
              {members(cube, dim).map((m) => (
                <option key={encode(m)} value={encode(m)}>{m === null ? NO_VALUE : m}</option>
              ))}
            </select>
          </label>
        );
      })}
      <span className="meta">Changing a scope re-runs the queries. No model call.</span>
    </div>
  );
}
