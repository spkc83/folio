import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, vi } from "vitest";
import { DiffView } from "./DiffView";

const diff = {
  changed_facts: [{ id: "a", label: "Fraud loss", before: "$52.6M", after: "$43.1M" }],
  added_facts: [{ id: "b", label: "Fraud rate · Marketplace", before: "", after: "2.79%" }],
  removed_facts: [], added_sections: [], removed_sections: [],
  definition_changes: [{ metric: "fraud_loss", from_version: 1, to_version: 2, definition: "Excluding friendly fraud." }],
};

beforeEach(() => vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(diff), { status: 200 }))));
afterEach(() => vi.unstubAllGlobals());

test("the difference view shows definitions and each changed value", async () => {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter initialEntries={["/r/r1/diff?from=1&to=2"]}>
        <Routes><Route path="/r/:id/diff" element={<DiffView />} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  expect(await screen.findByText("$43.1M")).toBeTruthy();
  expect(screen.getByText("$52.6M")).toBeTruthy();
  expect(screen.getByText(/fraud_loss changed from version 1 to version 2/)).toBeTruthy();
  expect(screen.getByText("Fraud rate · Marketplace")).toBeTruthy();
});
