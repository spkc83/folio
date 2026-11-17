/** Renders report parts with a claims payload, without a server. For the claims tests and screenshots. */
import { render } from "@testing-library/react";
import type { ReactNode } from "react";
import type { Payload } from "../lib/api";
import { claimsPayload } from "./claimsFixture";
import { FactsContext, QueriesContext } from "./context";
import { ScopeProvider } from "./ScopeContext";

export function ClaimsHarness({ data, children }: { data: Payload; children: ReactNode }) {
  return (
    <FactsContext.Provider value={data.facts}>
      <QueriesContext.Provider value={data.queries}>
        <ScopeProvider cube={data.cube ?? null} defs={data.query_defs ?? {}} reason={null} drill={null} pin={null} hosted={false}>
          {children}
        </ScopeProvider>
      </QueriesContext.Provider>
    </FactsContext.Provider>
  );
}

export function renderWith(node: (data: Payload) => ReactNode, data: Payload = claimsPayload()) {
  return render(<ClaimsHarness data={data}>{node(data)}</ClaimsHarness>);
}
