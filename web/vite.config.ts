import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [
    react(),
    {
      name: "normalize-packaged-html-line-endings",
      transformIndexHtml: (html) => html.replace(/\r\n?/g, "\n"),
    },
  ],
  build: {
    outDir: "../paper_agent/web/static",
    emptyOutDir: true,
  },
  server: {
    host: "127.0.0.1",
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
  test: {
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
    css: true,
  },
});
