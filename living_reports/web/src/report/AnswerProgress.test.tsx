import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { afterEach, vi } from "vitest";
import { AnswerProgress } from "./AnswerProgress";

afterEach(() => vi.unstubAllGlobals());

const show = (body: unknown, status = 200) => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(body), { status })));
  const client = new QueryClient();
  render(
    <QueryClientProvider client={client}>
      <AnswerProgress progressId="p-1234abcd" question="Where is fraud loss concentrated?" pollMs={20} />
    </QueryClientProvider>,
  );
};

test("progress: shows each done stage with its time and the stage that runs", async () => {
  show({ steps: [
    { key: "understand", label: "Understand your question", seconds: 1.2 },
    { key: "write", label: "Write the report and check each statement", seconds: null },
  ], seconds: 3, done: false });
  const panel = screen.getByRole("status", { name: "Answer progress" });
  expect(within(panel).getByText("“Where is fraud loss concentrated?”")).toBeTruthy();
  expect(await within(panel).findByText("1.2 s")).toBeTruthy();
  const running = within(panel).getByText("Write the report and check each statement…").closest("li")!;
  expect(running.className).toBe("running");
  expect(within(panel).getByText("This can take a minute or more.")).toBeTruthy();
  expect(panel.textContent).not.toMatch(/model/i);
});

test("progress: before the backend starts, the panel says so", async () => {
  show({ detail: "No answer is running under this id." }, 404);
  expect(await screen.findByText("Start the answer…")).toBeTruthy();
});

test("send button: gray while an answer runs or the box is empty", async () => {
  const { SendButton } = await import("./SendButton");
  const { rerender } = render(<SendButton busy={false} empty={false} />);
  expect((screen.getByRole("button", { name: "Send the question" }) as HTMLButtonElement).disabled).toBe(false);
  rerender(<SendButton busy={true} empty={false} />);
  expect((screen.getByRole("button", { name: "Building your answer" }) as HTMLButtonElement).disabled).toBe(true);
  rerender(<SendButton busy={false} empty={true} />);
  expect((screen.getByRole("button", { name: "Send the question" }) as HTMLButtonElement).disabled).toBe(true);
});
