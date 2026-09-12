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
        "category": "Stockage serveur",
        "price": 550,
        "description": "Stockage d'entrée de gamme pour PME, jusqu'à 10 utilisateurs.",
    },
    {
        "id": "gtx-pro",
        "name": "GTX Pro",
        "category": "Stockage serveur",
        "price": 4800,
        "description": "Stockage évolutif pour moyennes entreprises, réplication incluse.",
    },
    {
        "id": "mg-special",
        "name": "MG Special Edition",
        "category": "Sécurité réseau",
        "price": 22000,
        "description": "Pare-feu et supervision réseau, déploiement multi-sites.",
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
                    "role": "Directrice Marketing",
                    "email": "julie.martin@acme.example",
                    "phone": "+33600000001",
                    "stance": "champion",
                    "notes": "A initié le contact, enthousiaste après la démo produit.",
                },
                {
                    "name": "Marc Dubois",
                    "role": "Directeur Financier",
                    "email": "marc.dubois@acme.example",
                    "phone": None,
                    "stance": "decideur",
                    "notes": "Valide le budget. Jamais contacté directement à ce jour.",
                },
            ],
            "objections": [
                {
                    "text": (
                        "Nous ne pouvons pas changer de fournisseur avant la fin "
                        "du contrat en cours."
                    ),
                    "root_cause": "timing_contractuel",
                    "resolved": False,
                    "raised_at": two_months_ago.isoformat(),
                }
            ],
            "history": [
                {
                    "channel": "voice",
                    "summary": (
                        "Appel de découverte. Contrainte principale : contrat fournisseur en "
                        "cours jusqu'à fin d'année. Le produit n'est pas en cause."
                    ),
                    "occurred_at": two_months_ago.isoformat(),
                }
            ],
            "next_steps": "Vérifier l'échéance réelle du contrat actuel et impliquer le CFO.",
            "risk_notes": "Le décideur budgétaire n'a jamais été engagé directement.",
            "last_activity_at": two_months_ago.isoformat(),
        }
    }
