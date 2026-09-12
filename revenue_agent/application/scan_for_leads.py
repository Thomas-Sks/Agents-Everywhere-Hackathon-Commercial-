"""Use case: periodic CRM scan and detection of the opportunities worth handling.

Triggered by Trigger.dev (cron) or manually. Three guardrails give this use case its shape:

1. **Overlap window** — the HubSpot search index is *eventually consistent*; we systematically
   read back a little before the last pointer and dedupe by identifier, otherwise leads slip
   through the cracks.
2. **Triage before reasoning** — the sort is deterministic and free; only the opportunities
   that make the cut consume an LLM cycle.
3. **Per-scan cap** — a spike of CRM activity must not turn into an unpredictable bill. The
   overflow is deferred to the next tick, not lost.
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

# The very first scan is an **inventory**, not a delta: without it, an opportunity never seen
# before and not recently modified would stay invisible forever — and dormant deals are
# precisely the ones worth waking up. So we reach far back exactly once, then only ever process
# the changes.
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

        # The pointer only advances if the CRM answered: otherwise we would replay the missed
        # window as though it had been processed.
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
        """First scan: adopt the existing book of business without carpet-bombing it.

        Every deal is recorded with its current stage and, as the date of last action, its date
        of last modification. The intended consequence: an active deal triggers nothing (you do
        not re-engage 500 prospects on installation day), while a deal dormant for longer than
        the inactivity threshold is immediately eligible — that is the value delivered from the
        very first minute.
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
        """Full pagination, deduplicated by identifier."""
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
