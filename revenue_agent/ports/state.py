"""Port for the scanner's operational state.

Not to be confused with the CRM: the CRM is the sales truth (shared with humans), this state is
the agent's execution memory (where the last scan got to, which follow-ups are scheduled). The
two have different lifecycles and must not be mixed.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from revenue_agent.domain.triage import KnownDealState


class ScanStatePort(Protocol):
    def get_pointer(self) -> datetime | None:
        """Timestamp of the last successful scan, or None on the very first start-up."""
        ...

    def set_pointer(self, moment: datetime) -> None: ...

    def get_known_states(self) -> dict[str, KnownDealState]: ...

    def upsert_known_state(self, opportunity_id: str, state: KnownDealState) -> None: ...

    def schedule_follow_up(self, opportunity_id: str, due_at: datetime) -> None:
        """Schedules a re-engagement. Consumed by triage once the due date is reached."""
        ...

    def clear_follow_up(self, opportunity_id: str) -> None: ...
