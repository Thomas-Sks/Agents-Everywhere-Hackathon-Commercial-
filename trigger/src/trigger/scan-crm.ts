/**
 * Long-running orchestration — Trigger.dev.
 *
 * This is the piece that makes the agent genuinely autonomous: without it, someone has to
 * launch the scan by hand, and "autonomous sales agent" becomes a misnomer.
 *
 * Trigger.dev has no Python SDK; the chosen pattern is therefore a thin TypeScript task that
 * calls our FastAPI over HTTP. The reasoning stays entirely in Python, only the cadence lives
 * here.
 *
 * Two tasks, two roles:
 *
 * 1. `scanCrm` (cron) — the heartbeat. This is the primary mechanism: scheduled follow-ups are
 *    stored on the Python side and detected by triage, which guarantees a single source of
 *    truth for "when to wake this opportunity up".
 *
 * 2. `scheduleFollowUp` (durable waitpoint) — to schedule a one-off re-engagement without
 *    waiting for the next tick. Useful for demos ("I schedule a follow-up two minutes out and
 *    it fires on its own") and for a deadline that has to be accurate to the hour.
 */

import { logger, schedules, task, wait } from "@trigger.dev/sdk";

const API_BASE_URL = process.env.REVENUE_AGENT_API_URL ?? "http://localhost:8000";
const SCAN_SHARED_SECRET = process.env.SCAN_SHARED_SECRET ?? "";

type ScanReport = {
  deals_scannes: number;
  opportunites_detectees: number;
  opportunites_traitees: Array<{
    opportunite: string;
    declencheur: string;
    raison: string;
    routage: string;
    decision: string;
  }>;
  reportees_au_prochain_scan: number;
  crm_disponible: boolean;
};

async function runScan(): Promise<ScanReport> {
  const response = await fetch(`${API_BASE_URL}/scan`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Scan-Token": SCAN_SHARED_SECRET,
    },
  });

  if (!response.ok) {
    // We throw: Trigger.dev then applies its retry policy, rather than silently marking a
    // failed scan as successful.
    throw new Error(`Scan refusé par l'API (${response.status}) : ${await response.text()}`);
  }

  return (await response.json()) as ScanReport;
}

export const scanCrm = schedules.task({
  id: "scan-crm",
  // Every 5 minutes. The sizing constraint is the HubSpot Search API, capped at 4
  // requests/second shared across all objects: there is no point scanning more often.
  cron: "*/5 * * * *",
  run: async () => {
    const report = await runScan();

    logger.info("Scan du CRM terminé", {
      dealsScannes: report.deals_scannes,
      detectees: report.opportunites_detectees,
      traitees: report.opportunites_traitees.length,
      reportees: report.reportees_au_prochain_scan,
    });

    if (!report.crm_disponible) {
      // The pointer did not advance on the Python side: the window will be replayed on the
      // next tick.
      logger.warn("CRM indisponible pendant ce scan — fenêtre non consommée");
    }

    for (const item of report.opportunites_traitees) {
      logger.info(`Décision sur ${item.opportunite}`, {
        declencheur: item.declencheur,
        routage: item.routage,
        decision: item.decision,
      });
    }

    return report;
  },
});

export const scheduleFollowUp = task({
  id: "schedule-follow-up",
  run: async (payload: { opportunityId: string; delaySeconds: number }) => {
    logger.info(`Relance programmée pour ${payload.opportunityId}`, payload);

    // Durable waitpoint: the task is suspended and its resources released. It resumes
    // exactly here, even weeks later, with no process kept alive.
    await wait.for({ seconds: payload.delaySeconds });

    const report = await runScan();
    logger.info(`Reprise de ${payload.opportunityId} après attente`, {
      traitees: report.opportunites_traitees.length,
    });
    return report;
  },
});
