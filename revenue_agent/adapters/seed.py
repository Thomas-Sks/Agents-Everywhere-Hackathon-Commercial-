"""Demonstration dataset for the local adapters.

Populates the JSON CRM and the catalogue when they are empty, so that `make demo` produces
something meaningful without depending on a HubSpot account. Replaceable by
`scripts/load_dataset.py`, which imports the Kaggle "CRM Sales Opportunities" dataset.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from revenue_agent.adapters.catalog.json_file import PRODUCTS_KEY
from revenue_agent.adapters.crm.json_file import OPPORTUNITIES_KEY
from revenue_agent.adapters.storage.json_document import JsonDocument

DEMO_PRODUCTS = [
    {
        "id": "gtx-basic",
        "name": "GTX Basic",
        "category": "Server storage",
        "price": 550,
        "description": "Entry-level storage for SMBs, up to 10 users.",
    },
    {
        "id": "gtx-pro",
        "name": "GTX Pro",
        "category": "Server storage",
        "price": 4800,
        "description": "Scalable storage for mid-market companies, replication included.",
    },
    {
        "id": "mg-special",
        "name": "MG Special Edition",
        "category": "Network security",
        "price": 22000,
        "description": "Firewall and network monitoring, multi-site deployment.",
    },
]


def ensure_seeded(crm_document: JsonDocument, catalog_document: JsonDocument) -> None:
    with catalog_document.update() as data:
        if not data.get(PRODUCTS_KEY):
            data[PRODUCTS_KEY] = DEMO_PRODUCTS

    with crm_document.update() as data:
        if not data.get(OPPORTUNITIES_KEY):
            data[OPPORTUNITIES_KEY] = _demo_opportunities()


def _demo_opportunities() -> dict:
    now = datetime.now(UTC)
    two_months_ago = now - timedelta(days=62)

    return {
        "acme-co": {
            "company": "Acme Co",
            "stage": "qualification",
            "probability": 35,
            "amount": 68000,
            "stakeholders": [
                {
                    "name": "Julie Martin",
                    "role": "Marketing Director",
                    "email": "julie.martin@acme.example",
                    "phone": "+33600000001",
                    "stance": "champion",
                    "notes": "Initiated contact, enthusiastic after the product demo.",
                },
                {
                    "name": "Marc Dubois",
                    "role": "CFO",
                    "email": "marc.dubois@acme.example",
                    "phone": None,
                    "stance": "decision_maker",
                    "notes": "Signs off the budget. Never contacted directly to date.",
                },
            ],
            "objections": [
                {
                    "text": (
                        "We cannot switch suppliers before our current contract "
                        "ends."
                    ),
                    "root_cause": "contractual_timing",
                    "resolved": False,
                    "raised_at": two_months_ago.isoformat(),
                }
            ],
            "history": [
                {
                    "channel": "voice",
                    "summary": (
                        "Discovery call. Main constraint: supplier contract running "
                        "until year end. The product is not the issue."
                    ),
                    "occurred_at": two_months_ago.isoformat(),
                }
            ],
            "next_steps": "Check the real end date of the current contract and engage the CFO.",
            "risk_notes": "The budget decision maker has never been engaged directly.",
            "last_activity_at": two_months_ago.isoformat(),
        }
    }
