import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import { viteSingleFile } from "vite-plugin-singlefile";

// One self-contained page. Fonts and code sit inside the file, so it opens with no server.
export default defineConfig({
  plugins: [react(), viteSingleFile()],
  build: { outDir: "dist-export", assetsInlineLimit: 100_000_000, rollupOptions: { input: "export.html" } },
});
