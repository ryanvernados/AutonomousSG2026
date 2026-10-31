import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

// Keep transformed tests separate from stale caches belonging to other projects.
const result = spawnSync(
  process.execPath,
  [
    fileURLToPath(
      new URL("../node_modules/@playwright/test/cli.js", import.meta.url),
    ),
    "test",
    ...process.argv.slice(2),
  ],
  {
    stdio: "inherit",
    env: {
      ...process.env,
      PWTEST_CACHE_DIR:
        process.env.PWTEST_CACHE_DIR ||
        fileURLToPath(new URL("../.cache/playwright", import.meta.url)),
    },
  },
);
if (result.error) console.error(result.error.message);
process.exit(result.status ?? 1);
