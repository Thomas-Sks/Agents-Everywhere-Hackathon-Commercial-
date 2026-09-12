/**
 * Orchestration longue durée — Trigger.dev.
 *
 * C'est la brique qui rend l'agent réellement autonome : sans elle, quelqu'un doit lancer le
 * scan à la main, et « agent commercial autonome » devient un abus de langage.
 *
 * Trigger.dev n'a pas de SDK Python ; le pattern retenu est donc une tâche TypeScript mince
 * qui appelle notre API FastAPI en HTTP. Le raisonnement reste intégralement en Python, seule
 * la cadence vit ici.
 *
 * Deux tâches, deux rôles :
 *
 * 1. `scanCrm` (cron) — le battement de cœur. C'est le mécanisme principal : les relances
 *    programmées sont stockées côté Python et détectées par le triage, ce qui garantit une
 *    source de vérité unique pour « quand réveiller cette opportunité ».
 *
 * 2. `scheduleFollowUp` (waitpoint durable) — pour programmer une reprise ponctuelle sans
 *    attendre le prochain tick. Utile en démo (« je programme une relance dans deux minutes,
 *    elle part toute seule ») et pour une échéance précise à l'heure près.
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
    // On lève : Trigger.dev applique alors sa politique de retry, plutôt que de marquer
    // silencieusement un scan raté comme réussi.
    throw new Error(`Scan refusé par l'API (${response.status}) : ${await response.text()}`);
  }

  return (await response.json()) as ScanReport;
}

export const scanCrm = schedules.task({
  id: "scan-crm",
  // Toutes les 5 minutes. La contrainte dimensionnante est la Search API HubSpot, plafonnée
  // à 4 requêtes/seconde partagées entre tous les objets : inutile de scanner plus souvent.
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
      // Le pointeur n'a pas avancé côté Python : la fenêtre sera rejouée au prochain tick.
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

    // Waitpoint durable : la tâche est suspendue et ses ressources libérées. Elle reprend
    // exactement ici, même des semaines plus tard, sans process maintenu en vie.
    await wait.for({ seconds: payload.delaySeconds });

    const report = await runScan();
    logger.info(`Reprise de ${payload.opportunityId} après attente`, {
      traitees: report.opportunites_traitees.length,
    });
    return report;
  },
});
