import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, vi } from "vitest";
import type { EditionState } from "../lib/api";
import { ControlRail } from "./ControlRail";

const state: EditionState = {
  number: 1, content_hash: "h1", tier: "team", status: "pending", author: "ana", newest: 1, can_publish: false, needs: ["owner"], signatures: [],
  checks: [
    { step: "trace", status: "pass", findings: [] },
    { step: "totals", status: "fail", findings: [{ where: "q1", detail: "Rows sum to 5, total is 6." }] },
    { step: "rules", status: "pass", findings: [] },
    { step: "wording", status: "open", findings: [{ where: "drives", detail: "States a cause." }] },
  ],
};

let publication: unknown;
let post: { status: number; body: unknown };
let pageState: unknown;

beforeEach(() => {
  publication = { edition: state, draft_changed: false };
  post = { status: 200, body: state };
  pageState = state;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    if (init) return new Response(JSON.stringify(post.body), { status: post.status });
    const body = url.endsWith("/state") ? pageState : publication;
    return new Response(JSON.stringify(body), { status: 200 });
  }));
  vi.stubGlobal("localStorage", { getItem: () => "omar", setItem: () => undefined });
});
afterEach(() => vi.unstubAllGlobals());

function show(edition: number | null = null) {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter>
        <ControlRail reportId="r1" edition={edition} tier="team" cost={{ usd: 0.004 }} demo />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

test("a failed check disables Publish and names the finding", async () => {
  show();
  expect(await screen.findByText("Rows sum to 5, total is 6.")).toBeTruthy();
  expect((screen.getByRole("button", { name: "Publish" }) as HTMLButtonElement).disabled).toBe(true);
});

test("each check has a word as well as a color", async () => {
  show();
  expect(await screen.findByText("Fail")).toBeTruthy();
  expect(screen.getAllByText("Pass").length).toBe(2);
  expect(screen.getByText("Open")).toBeTruthy();
});

test("a stale action shows the current edition with a link", async () => {
  const user = userEvent.setup();
  post = { status: 409, body: { detail: "A newer edition exists.", current_edition: 2 } };
  publication = { edition: { ...state, checks: state.checks.filter((c) => c.status === "pass") }, draft_changed: false };
  show();
  await user.click(await screen.findByRole("button", { name: "Sign" }));
  expect(await screen.findByText("A newer edition exists.")).toBeTruthy();
  expect(screen.getByRole("link", { name: "Open edition 2" }).getAttribute("href")).toBe("/r/r1/e/2");
});

test("an older edition shows a newer-edition notice", async () => {
  pageState = { ...state, number: 1, newest: 3 };
  show(1);
  expect((await screen.findByRole("link", { name: "A newer edition exists" })).getAttribute("href")).toBe("/r/r1/e/3");
});

test("an old edition page shows its own checks and offers no signature", async () => {
  publication = { edition: { ...state, number: 3, newest: 3, checks: [] }, draft_changed: false };
  pageState = { ...state, number: 1, newest: 3 };
  show(1);
  expect(await screen.findByText("Rows sum to 5, total is 6.")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Sign" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Publish" })).toBeNull();
});

test("the current edition page can be signed with its own hash", async () => {
  const user = userEvent.setup();
  pageState = { ...state, number: 2, newest: 2, content_hash: "own", checks: [] };
  publication = { edition: { ...state, number: 2, newest: 2, content_hash: "other" }, draft_changed: false };
  show(2);
  await user.click(await screen.findByRole("button", { name: "Sign" }));
  const call = (fetch as unknown as { mock: { calls: [string, RequestInit?][] } }).mock.calls.find((c) => c[1]);
  expect(call?.[0]).toContain("/editions/2/sign");
  expect(call?.[1]?.body).toContain("own");
});

test("only a published edition offers a download", async () => {
  show();
  await screen.findByRole("button", { name: "Publish" });
  expect(screen.queryByRole("link", { name: "Download as HTML" })).toBeNull();
});

test("a pending edition page says so", async () => {
  show(1);
  expect(await screen.findByText("This edition is not published.")).toBeTruthy();
});

test("a draft with changes offers to run the checks", async () => {
  publication = { edition: null, draft_changed: true };
  show();
  expect(await screen.findByRole("button", { name: "Run checks" })).toBeTruthy();
});

test("the cost is shown, and an unknown cost says so", async () => {
  show();
  expect(await screen.findByText("This answer cost $0.0040")).toBeTruthy();
});
