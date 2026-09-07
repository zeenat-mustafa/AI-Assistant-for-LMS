import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

const alias = { "@": fileURLToPath(new URL("./src", import.meta.url)) };

/**
 * Two projects rather than one environment.
 *
 * `unit` keeps the 5.1 API-client tests in a plain node environment, where
 * Node's own `fetch`/`Response`/`ReadableStream` are used unmodified.
 * `dom` runs the React component tests under jsdom with Testing Library.
 * (Vitest 5 removed `environmentMatchGlobs`; `projects` is its replacement.)
 */
export default defineConfig({
  test: {
    projects: [
      {
        resolve: { alias },
        test: {
          name: "unit",
          environment: "node",
          include: ["src/**/*.test.ts"],
        },
      },
      {
        resolve: { alias },
        test: {
          name: "dom",
          environment: "jsdom",
          include: ["src/**/*.test.tsx"],
          setupFiles: ["./src/test/setup-dom.ts"],
        },
      },
    ],
  },
});
