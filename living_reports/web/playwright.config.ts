import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "e2e",
  timeout: 120_000,
  workers: 1,
  use: { baseURL: "http://localhost:3100" },
  expect: { timeout: 30_000 },
  webServer: [
    {
      command: ".venv/bin/python -m uvicorn living_reports.api.app:create_app --factory --port 8100",
      cwd: "../..",
      url: "http://localhost:8100/api/health",
      reuseExistingServer: true,
      timeout: 120_000,
    },
    { command: "npm run dev", url: "http://localhost:3100", reuseExistingServer: true },
  ],
});
