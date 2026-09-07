import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

/**
 * Config for the LIVE verification run (`npm run verify:api`), which talks to
 * a real backend on NEXT_PUBLIC_API_BASE_URL. Deliberately a separate config
 * from vitest.config.mts so `npm test` can never make a real network call.
 */
export default defineConfig({
  test: {
    environment: "node",
    include: ["scripts/**/*.live.ts"],
    testTimeout: 30_000,
    // Real HTTP against one dev backend -- keep the calls in order.
    fileParallelism: false,
  },
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
});
