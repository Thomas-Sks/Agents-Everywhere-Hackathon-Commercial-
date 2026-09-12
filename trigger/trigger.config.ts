import { defineConfig } from "@trigger.dev/sdk";

/**
 * Trigger.dev configuration.
 *
 * The project reference is created on their dashboard and cannot be guessed: it is therefore
 * read from the environment rather than hard-coded, so a deployment needs no edit to this
 * file. The CLI's `--project-ref` flag takes precedence if needed.
 *
 * `dirs` is resolved relative to the location of this file.
 */
export default defineConfig({
  project: process.env.TRIGGER_PROJECT_REF ?? "proj_to_be_filled_in",
  dirs: ["./src/trigger"],
  // One hour: the scan calls our API, which may chain several decision cycles.
  maxDuration: 3600,
  retries: {
    // In development a failure must be visible immediately rather than masked by successive
    // retries.
    enabledInDev: false,
    default: {
      maxAttempts: 3,
      minTimeoutInMs: 1000,
      maxTimeoutInMs: 10000,
      factor: 2,
      randomize: true,
    },
  },
});
