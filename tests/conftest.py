"""Test doubles implementing the ports.

This is where the value of the hexagonal architecture becomes measurable: every piece of
business behaviour — triage, stakes-based routing, channel resolution, the scan loop — can be
exercised without HubSpot, without OpenRouter and without a network.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta

import pytest

from revenue_agent.domain.errors import OpportunityNotFound
from revenue_agent.domain.models import (
    CallOutcome,
    Interaction,
    Objection,
    Opportunity,
    Page,
    ProspectInsight,
    Stakeholder,
    Stance,
)
from revenue_agent.domain.triage import KnownDealState

NOW = datetime(2026, 9, 12, 10, 0, tzinfo=UTC)


@dataclass
class FakeCrm:
    opportunities: dict[str, Opportunity] = field(default_factory=dict)
    snapshots: list = field(default_factory=list)
    interactions: list[tuple[str, Interaction]] = field(default_factory=list)
    updates: list[dict] = field(default_factory=list)
    logged_calls: list[CallOutcome] = field(default_factory=list)
    stakeholder_updates: list[dict] = field(default_factory=list)
    fail_on_search: bool = False

    def find_modified_since(self, since, cursor=None, page_size=100) -> Page:
        if self.fail_on_search:
            from revenue_agent.domain.errors import CrmError

            raise CrmError("fake", "indisponible")
        return Page(items=tuple(self.snapshots), next_cursor=None)

    def load_opportunity(self, opportunity_id: str) -> Opportunity:
        try:
            return self.opportunities[opportunity_id]
        except KeyError:
            raise OpportunityNotFound(opportunity_id) from None

    def record_interaction(self, opportunity_id: str, interaction: Interaction) -> None:
        self.interactions.append((opportunity_id, interaction))

    def update_opportunity(
        self,
        opportunity_id: str,
        *,
        stage=None,
        probability=None,
        objection: Objection | None = None,
        next_steps=None,
    ) -> None:
        self.updates.append(
            {
                "opportunity_id": opportunity_id,
                "stage": stage,
                "probability": probability,
                "objection": objection,
                "next_steps": next_steps,
            }
        )

    def update_stakeholder(
        self, opportunity_id: str, *, name: str, stance: Stance, role: str = "", notes: str = ""
    ) -> None:
        opportunity = self.opportunities.get(opportunity_id)
        if opportunity is None:
            raise OpportunityNotFound(opportunity_id)

        self.stakeholder_updates.append(
            {"opportunity_id": opportunity_id, "name": name, "stance": stance, "role": role}
        )

        existing = {s.name.casefold(): s for s in opportunity.stakeholders}
        updated = existing.get(name.casefold())
        if updated is None:
            merged = (*opportunity.stakeholders, Stakeholder(name=name, role=role, stance=stance))
        else:
            merged = tuple(
                replace(s, stance=stance, role=role or s.role, notes=notes or s.notes)
                if s.name.casefold() == name.casefold()
                else s
                for s in opportunity.stakeholders
            )
        self.opportunities[opportunity_id] = replace(opportunity, stakeholders=merged)

    def resolve_objection(self, opportunity_id: str, objection_id: str, resolution: str) -> bool:
        opportunity = self.opportunities.get(opportunity_id)
        if opportunity is None:
            return False
        from dataclasses import replace as _replace

        updated = tuple(
            _replace(objection, resolved=True, resolution=resolution)
            if objection.id == objection_id
            else objection
            for objection in opportunity.objections
        )
        if updated == opportunity.objections:
            return False
        self.opportunities[opportunity_id] = _replace(opportunity, objections=updated)
        return True

    def find_opportunity_by_phone(self, phone_number: str) -> str | None:
        digits = "".join(c for c in phone_number if c.isdigit())
        for opportunity_id, opportunity in self.opportunities.items():
            for stakeholder in opportunity.stakeholders:
                if not stakeholder.phone:
                    continue
                if "".join(c for c in stakeholder.phone if c.isdigit()) == digits:
                    return opportunity_id
        return None

    def log_call(self, opportunity_id: str, outcome: CallOutcome) -> None:
        self.logged_calls.append(outcome)


@dataclass
class InMemoryScanState:
    pointer: datetime | None = None
    states: dict[str, KnownDealState] = field(default_factory=dict)

    def get_pointer(self) -> datetime | None:
        return self.pointer

    def set_pointer(self, moment: datetime) -> None:
        self.pointer = moment

    def get_known_states(self) -> dict[str, KnownDealState]:
        return dict(self.states)

    def upsert_known_state(self, opportunity_id: str, state: KnownDealState) -> None:
        self.states[opportunity_id] = state

    def schedule_follow_up(self, opportunity_id: str, due_at: datetime) -> None:
        current = self.states.get(opportunity_id, KnownDealState())
        self.states[opportunity_id] = KnownDealState(
            stage=current.stage,
            last_decision_at=current.last_decision_at,
            follow_up_due_at=due_at,
        )

    def clear_follow_up(self, opportunity_id: str) -> None:
        current = self.states.get(opportunity_id)
        if current is not None:
            self.states[opportunity_id] = KnownDealState(
                stage=current.stage,
                last_decision_at=current.last_decision_at,
                follow_up_due_at=None,
            )


@dataclass
class RecordingAgent:
    response: str = "Décision prise."
    calls: list[dict] = field(default_factory=list)

    def decide(self, *, opportunity: Opportunity, event: str, strategic: bool = False) -> str:
        self.calls.append(
            {"opportunity_id": opportunity.id, "event": event, "strategic": strategic}
        )
        return self.response


@dataclass
class RecordingEmail:
    sent: list[dict] = field(default_factory=list)

    def send(self, *, to: str, subject: str, body: str) -> str:
        self.sent.append({"to": to, "subject": subject, "body": body})
        return "msg-1"


@dataclass
class RecordingWhatsApp:
    sent: list[dict] = field(default_factory=list)

    def send(self, *, to_phone_number: str, message: str) -> str:
        self.sent.append({"to": to_phone_number, "message": message})
        return "wa-1"


@dataclass
class RecordingVoice:
    calls: list[dict] = field(default_factory=list)

    def place_call(self, *, to_phone_number: str, opportunity: Opportunity, objective: str) -> str:
        self.calls.append({"to": to_phone_number, "objective": objective})
        return "call-1"


@dataclass
class RecordingHandoff:
    escalations: list[dict] = field(default_factory=list)

    notifications: list[dict] = field(default_factory=list)

    def escalate(self, *, opportunity: Opportunity, reason: str, urgency: str, context_brief: str):
        self.escalations.append(
            {"opportunity": opportunity.id, "reason": reason, "urgency": urgency}
        )

    def notify_pending_approval(
        self, *, opportunity: Opportunity, approval_id: str, channel: str, reason: str, preview: str
    ) -> None:
        self.notifications.append(
            {"opportunity": opportunity.id, "approval_id": approval_id, "channel": channel}
        )


@dataclass
class InMemoryApprovals:
    submitted: list = field(default_factory=list)
    sent: dict[str, int] = field(default_factory=dict)
    _by_id: dict = field(default_factory=dict)

    def submit(self, approval):
        from dataclasses import replace

        stored = replace(approval, id=approval.id or f"ap-{len(self.submitted) + 1}")
        self.submitted.append(stored)
        self._by_id[stored.id] = stored
        return stored

    def get(self, approval_id: str):
        return self._by_id.get(approval_id)

    def list_pending(self) -> list:
        return [a for a in self._by_id.values() if a.is_pending]

    def mark(self, approval_id: str, status, reviewer: str, note: str = ""):
        from dataclasses import replace

        approval = self._by_id.get(approval_id)
        if approval is None:
            return None
        updated = replace(approval, status=status, reviewer=reviewer, review_note=note)
        self._by_id[approval_id] = updated
        return updated

    def count_recent_outbound(self, opportunity_id: str, hours: int = 24) -> int:
        return self.sent.get(opportunity_id, 0)

    def record_sent(self, opportunity_id: str) -> None:
        self.sent[opportunity_id] = self.sent.get(opportunity_id, 0) + 1


@dataclass
class StubCatalog:
    products: list = field(default_factory=list)

    def list_products(self) -> list:
        return list(self.products)

    def get_product(self, product_id: str):
        return next((p for p in self.products if p.id == product_id), None)


@dataclass
class StubEnrichment:
    insights: list[ProspectInsight] = field(default_factory=list)

    def research_company(self, company_name: str, *, since_days: int = 90, limit: int = 5):
        return list(self.insights)


def build_opportunity(
    opportunity_id: str = "acme-co",
    *,
    stage: str = "qualification",
    amount: float | None = None,
    email: str | None = "julie@acme.example",
    phone: str | None = "+33600000001",
    objections: tuple[Objection, ...] = (),
) -> Opportunity:
    return Opportunity(
        id=opportunity_id,
        company="Acme Co",
        stage=stage,
        amount=amount,
        stakeholders=(
            Stakeholder(
                name="Julie Martin",
                role="Marketing Director",
                email=email,
                phone=phone,
                stance=Stance.CHAMPION,
            ),
        ),
        objections=objections,
        last_activity_at=NOW - timedelta(days=1),
    )


@pytest.fixture
def crm() -> FakeCrm:
    return FakeCrm(opportunities={"acme-co": build_opportunity()})


@pytest.fixture
def scan_state() -> InMemoryScanState:
    return InMemoryScanState()


@pytest.fixture
def agent() -> RecordingAgent:
    return RecordingAgent()
