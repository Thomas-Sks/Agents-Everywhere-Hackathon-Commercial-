"""Sales data ports — what the application expects of a CRM, without knowing which one.

Implemented by `adapters/crm/hubspot.py` (real) and `adapters/crm/json_file.py` (local).
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from revenue_agent.domain.models import (
    CallOutcome,
    Interaction,
    Objection,
    Opportunity,
    Page,
    Product,
    Stance,
)


class CrmPort(Protocol):
    """Reading/writing opportunities. Raises `CrmError` when the external system fails."""

    def find_modified_since(
        self, since: datetime, cursor: str | None = None, page_size: int = 100
    ) -> Page:
        """Deals modified since `since`, paginated by cursor.

        The implementation may rely on an *eventually consistent* index: the caller queries
        with an overlap window and dedupes.
        """
        ...

    def load_opportunity(self, opportunity_id: str) -> Opportunity:
        """Full context. Raises `OpportunityNotFound` if the identifier is unknown."""
        ...

    def record_interaction(self, opportunity_id: str, interaction: Interaction) -> None: ...

    def update_opportunity(
        self,
        opportunity_id: str,
        *,
        stage: str | None = None,
        probability: int | None = None,
        objection: Objection | None = None,
        next_steps: str | None = None,
    ) -> None: ...

    def update_stakeholder(
        self,
        opportunity_id: str,
        *,
        name: str,
        stance: Stance,
        role: str = "",
        notes: str = "",
    ) -> None:
        """Records who decides, who influences, who blocks.

        A complex sale rarely fails on the product alone, so the agent must be able to write
        this map, not just read it — a stance it cannot record is a stance it forgets at the
        next cycle.

        A name unknown to the CRM creates a stakeholder without contact details: the budget
        holder who has never been contacted is precisely the one who decides, and leaving them
        out of the map is what makes a deal look healthy right up until it dies.
        """
        ...

    def resolve_objection(self, opportunity_id: str, objection_id: str, resolution: str) -> bool:
        """Closes an objection that has been dealt with. Returns False if the id is unknown.

        Without this write path, objections pile up indefinitely: the context gets polluted on
        every cycle and stakes-based routing stays stuck on the most expensive model, since it
        keys off the presence of open objections.
        """
        ...

    def find_opportunity_by_phone(self, phone_number: str) -> str | None:
        """Finds an opportunity from a contact's phone number.

        Must be resolved on the CRM side (indexed search), never by sweeping the book of
        business: this is called on every inbound message.
        """
        ...

    def log_call(self, opportunity_id: str, outcome: CallOutcome) -> None:
        """Records a real phone call (transcript, duration, outcome)."""
        ...


class CatalogPort(Protocol):
    """Product sheets — prices and specifications the agent is not allowed to invent."""

    def list_products(self) -> list[Product]: ...

    def get_product(self, product_id: str) -> Product | None: ...
