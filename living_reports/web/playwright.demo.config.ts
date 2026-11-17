import { defineConfig } from "@playwright/test";

// The eight demo steps on real data, with a deterministic stand-in for the model.
const dir = `/tmp/lr-demo-${process.pid}`;

export default defineConfig({
  testDir: "e2e-demo",
  timeout: 480_000,
  workers: 1,
  expect: { timeout: 40_000 },
  use: { baseURL: "http://localhost:3101" },
  webServer: [
    {
      command: ".venv/bin/python -m uvicorn tests.living_reports.e2e_app:create --factory --port 8101",
      cwd: "../..",
      env: { LR_DATA_DIR: dir, LR_DEMO: "retail", PYTHONPATH: "." },
      url: "http://localhost:8101/api/health",
      reuseExistingServer: false,
      timeout: 180_000,
    },
    {
      command: "npx vite --port 3101",
      env: { LR_API_URL: "http://localhost:8101" },
      url: "http://localhost:3101",
      reuseExistingServer: false,
    },
  ],
});
