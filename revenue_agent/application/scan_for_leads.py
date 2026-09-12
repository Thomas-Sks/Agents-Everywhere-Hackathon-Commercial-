"""Use case : scan périodique du CRM et détection des opportunités à traiter.

Déclenché par Trigger.dev (cron) ou manuellement. Trois garde-fous structurent ce use case :

1. **Fenêtre de recouvrement** — l'index de recherche HubSpot est *eventually consistent* ; on
   relit systématiquement un peu avant le dernier pointeur et on déduplique par identifiant,
   faute de quoi des leads passent entre les mailles.
2. **Triage avant raisonnement** — le tri est déterministe et gratuit ; seules les
   opportunités retenues consomment un cycle LLM.
3. **Plafond par scan** — un pic d'activité dans le CRM ne doit pas se traduire par une
   facture imprévisible. Le surplus est reporté au tick suivant, pas perdu.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from revenue_agent.application.run_decision_cycle import RunDecisionCycle
from revenue_agent.config import ScanSettings
from revenue_agent.domain.errors import CrmError
from revenue_agent.domain.models import DealSnapshot
from revenue_agent.domain.triage import KnownDealState, Trigger, select_triggers
from revenue_agent.ports.crm import CrmPort
from revenue_agent.ports.state import ScanStatePort

logger = logging.getLogger(__name__)

# Le tout premier scan est un **inventaire**, pas un delta : sans cela, une opportunité jamais
# vue et non modifiée récemment resterait invisible pour toujours — or les deals dormants sont
# précisément ceux qu'il faut réveiller. On remonte donc loin une seule fois, puis on ne traite
# plus que les variations.
BOOTSTRAP_HORIZON = timedelta(days=3650)
MAX_PAGES = 20


@dataclass(frozen=True, slots=True)
class ProcessedOpportunity:
    opportunity_id: str
    trigger: str
    reason: str
    strategic: bool
    summary: str


@dataclass(frozen=True, slots=True)
class ScanReport:
    scanned_deals: int
    detected: int
    processed: tuple[ProcessedOpportunity, ...]
    deferred: int
    crm_available: bool

    def as_dict(self) -> dict:
        return {
            "deals_scannes": self.scanned_deals,
            "opportunites_detectees": self.detected,
            "opportunites_traitees": [
                {
                    "opportunite": item.opportunity_id,
                    "declencheur": item.trigger,
                    "raison": item.reason,
                    "routage": "stratégique" if item.strategic else "routine",
                    "decision": item.summary,
                }
                for item in self.processed
            ],
            "reportees_au_prochain_scan": self.deferred,
            "crm_disponible": self.crm_available,
        }


class ScanForLeads:
    def __init__(
        self,
        *,
        crm: CrmPort,
        scan_state: ScanStatePort,
        decision_cycle: RunDecisionCycle,
        settings: ScanSettings,
    ) -> None:
        self._crm = crm
        self._scan_state = scan_state
        self._decision_cycle = decision_cycle
        self._settings = settings

    def execute(self) -> ScanReport:
        now = datetime.now(UTC)
        is_bootstrap = self._scan_state.get_pointer() is None
        since = self._window_start(now)

        snapshots, crm_available = self._collect_snapshots(since)

        if is_bootstrap:
            self._adopt_existing_book(snapshots)

        known_states = self._scan_state.get_known_states()

        triggers = select_triggers(
            snapshots=snapshots,
            known_states=known_states,
            now=now,
            inactivity_days=self._settings.inactivity_days,
        )

        budget = self._settings.max_decisions_per_scan
        selected, deferred = triggers[:budget], max(0, len(triggers) - budget)
        if deferred:
            logger.info(
                "%s opportunité(s) au-delà du plafond de %s — reportées au prochain scan",
                deferred,
                budget,
            )

        processed = tuple(filter(None, (self._process(trigger) for trigger in selected)))

        # Le pointeur n'avance que si le CRM a répondu : sinon on rejouerait la fenêtre
        # manquée comme si elle avait été traitée.
        if crm_available:
            self._scan_state.set_pointer(now)

        return ScanReport(
            scanned_deals=len(snapshots),
            detected=len(triggers),
            processed=processed,
            deferred=deferred,
            crm_available=crm_available,
        )

    def _window_start(self, now: datetime) -> datetime:
        pointer = self._scan_state.get_pointer()
        if pointer is None:
            return now - BOOTSTRAP_HORIZON
        return pointer - timedelta(minutes=self._settings.overlap_minutes)

    def _adopt_existing_book(self, snapshots: list[DealSnapshot]) -> None:
        """Premier scan : on adopte le portefeuille existant sans le bombarder.

        Chaque deal est enregistré avec son stade actuel et, comme date de dernière action, sa
        date de dernière modification. Conséquence voulue : un deal actif ne déclenche rien
        (on ne relance pas 500 prospects le jour de l'installation), tandis qu'un deal dormant
        depuis plus longtemps que le seuil d'inactivité est immédiatement éligible — c'est la
        valeur qu'on apporte dès la première minute.
        """
        for snapshot in snapshots:
            self._scan_state.upsert_known_state(
                snapshot.id,
                KnownDealState(
                    stage=snapshot.stage,
                    last_decision_at=snapshot.last_modified_at,
                    follow_up_due_at=None,
                ),
            )
        logger.info("Premier scan : %s opportunité(s) adoptée(s) depuis le CRM", len(snapshots))

    def _collect_snapshots(self, since: datetime) -> tuple[list[DealSnapshot], bool]:
        """Pagination complète, dédupliquée par identifiant."""
        by_id: dict[str, DealSnapshot] = {}
        cursor: str | None = None

        for page_number in range(MAX_PAGES):
            try:
                page = self._crm.find_modified_since(
                    since, cursor=cursor, page_size=self._settings.page_size
                )
            except CrmError:
                logger.exception("CRM indisponible pendant le scan — pointeur non avancé")
                return list(by_id.values()), False

            for snapshot in page.items:
                by_id[snapshot.id] = snapshot

            cursor = page.next_cursor
            if not cursor:
                break
            if page_number == MAX_PAGES - 1:
                logger.warning(
                    "Plafond de %s pages atteint — le reste sera repris au prochain scan",
                    MAX_PAGES,
                )

        return list(by_id.values()), True

    def _process(self, trigger: Trigger) -> ProcessedOpportunity | None:
        result = self._decision_cycle.execute_safely(
            trigger.opportunity_id, trigger.reason, trigger_kind=trigger.kind
        )
        if result is None:
            return None
        return ProcessedOpportunity(
            opportunity_id=trigger.opportunity_id,
            trigger=trigger.kind.value,
            reason=trigger.reason,
            strategic=result.strategic,
            summary=result.summary,
        )
