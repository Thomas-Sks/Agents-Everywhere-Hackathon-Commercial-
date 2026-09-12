import { defineConfig } from "@trigger.dev/sdk";

/**
 * Configuration Trigger.dev.
 *
 * La référence de projet est créée sur leur dashboard et ne peut pas être devinée : elle est
 * donc lue depuis l'environnement plutôt que codée en dur, ce qui permet de déployer sans
 * modifier ce fichier. Le drapeau `--project-ref` du CLI l'emporte si besoin.
 *
 * `dirs` est résolu relativement à l'emplacement de ce fichier.
 */
export default defineConfig({
  project: process.env.TRIGGER_PROJECT_REF ?? "proj_a_renseigner",
  dirs: ["./src/trigger"],
  // Une heure : le scan appelle notre API, qui peut enchaîner plusieurs cycles de décision.
  maxDuration: 3600,
  retries: {
    // En développement, un échec doit être visible immédiatement plutôt que masqué par des
    // tentatives successives.
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
