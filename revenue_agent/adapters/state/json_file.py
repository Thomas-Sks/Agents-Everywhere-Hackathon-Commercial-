"""État opérationnel du scanner sur fichier JSON — implémente `ScanStatePort`.

Contient le pointeur du dernier scan et, par opportunité, le dernier stade connu, la date de
la dernière décision et l'éventuelle relance programmée. C'est la mémoire d'exécution de
l'agent, distincte de la vérité commerciale qui, elle, vit dans le CRM.
"""

from __future__ import annotations

from datetime import UTC, datetime

from revenue_agent.adapters.storage.json_document import JsonDocument
from revenue_agent.domain.triage import KnownDealState

POINTER_KEY = "scan_pointer"
DEALS_KEY = "known_deals"


class JsonFileScanStateAdapter:
    def __init__(self, document: JsonDocument) -> None:
        self._document = document

    def get_pointer(self) -> datetime | None:
        return _parse_datetime(self._document.read().get(POINTER_KEY))

    def set_pointer(self, moment: datetime) -> None:
        with self._document.update() as data:
            data[POINTER_KEY] = moment.isoformat()

    def get_known_states(self) -> dict[str, KnownDealState]:
        raw_states = self._document.read().get(DEALS_KEY, {})
        return {
            opportunity_id: KnownDealState(
                stage=raw.get("stage", ""),
                last_decision_at=_parse_datetime(raw.get("last_decision_at")),
                follow_up_due_at=_parse_datetime(raw.get("follow_up_due_at")),
            )
            for opportunity_id, raw in raw_states.items()
        }

    def upsert_known_state(self, opportunity_id: str, state: KnownDealState) -> None:
        with self._document.update() as data:
            deals = data.setdefault(DEALS_KEY, {})
            deals[opportunity_id] = {
                "stage": state.stage,
                "last_decision_at": _serialise_datetime(state.last_decision_at),
                "follow_up_due_at": _serialise_datetime(state.follow_up_due_at),
            }

    def schedule_follow_up(self, opportunity_id: str, due_at: datetime) -> None:
        with self._document.update() as data:
            deals = data.setdefault(DEALS_KEY, {})
            entry = deals.setdefault(opportunity_id, {})
            entry["follow_up_due_at"] = due_at.isoformat()

    def clear_follow_up(self, opportunity_id: str) -> None:
        with self._document.update() as data:
            entry = data.get(DEALS_KEY, {}).get(opportunity_id)
            if entry is not None:
                entry["follow_up_due_at"] = None


def _parse_datetime(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _serialise_datetime(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment else None
