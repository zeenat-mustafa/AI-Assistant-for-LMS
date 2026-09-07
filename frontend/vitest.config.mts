import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

/**
 * Lightweight test setup for the API client (5.1 scaffold verification).
 *
 * `environment: "node"` on purpose -- these tests exercise plain modules with
 * a stubbed `fetch`/`localStorage`, so there is no need to pull in jsdom.
 * Component tests, if 5.2+ needs them, can switch to a browser-like env then.
 */
export default defineConfig({
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
});
