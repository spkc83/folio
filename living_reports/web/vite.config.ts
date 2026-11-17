/// <reference types="vitest" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const API = process.env.LR_API_URL ?? "http://localhost:8100";

export default defineConfig({
  plugins: [react()],
  server: { strictPort: true, proxy: { "/api": API } },
  test: { environment: "jsdom", globals: true, include: ["src/**/*.test.tsx"] },
});
