"""Port d'état opérationnel du scanner.

À ne pas confondre avec le CRM : le CRM est la vérité commerciale (partagée avec les humains),
cet état est la mémoire d'exécution de l'agent (où en était le dernier scan, quelles relances
sont programmées). Les deux ont des cycles de vie différents et ne doivent pas se mélanger.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from revenue_agent.domain.triage import KnownDealState


class ScanStatePort(Protocol):
    def get_pointer(self) -> datetime | None:
        """Horodatage du dernier scan réussi, ou None au tout premier démarrage."""
        ...

    def set_pointer(self, moment: datetime) -> None: ...

    def get_known_states(self) -> dict[str, KnownDealState]: ...

    def upsert_known_state(self, opportunity_id: str, state: KnownDealState) -> None: ...

    def schedule_follow_up(self, opportunity_id: str, due_at: datetime) -> None:
        """Programme une reprise. Consommée par le triage quand l'échéance est atteinte."""
        ...

    def clear_follow_up(self, opportunity_id: str) -> None: ...
