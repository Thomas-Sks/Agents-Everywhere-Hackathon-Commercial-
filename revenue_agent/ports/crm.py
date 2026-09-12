"""Ports de données commerciales — ce que l'application attend d'un CRM, sans savoir lequel.

Implémentés par `adapters/crm/hubspot.py` (réel) et `adapters/crm/json_file.py` (local).
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
)


class CrmPort(Protocol):
    """Lecture/écriture des opportunités. Lève `CrmError` en cas d'échec du système externe."""

    def find_modified_since(
        self, since: datetime, cursor: str | None = None, page_size: int = 100
    ) -> Page:
        """Deals modifiés depuis `since`, paginés par curseur.

        L'implémentation peut s'appuyer sur un index *eventually consistent* : l'appelant
        interroge avec une fenêtre de recouvrement et déduplique.
        """
        ...

    def load_opportunity(self, opportunity_id: str) -> Opportunity:
        """Contexte complet. Lève `OpportunityNotFound` si l'identifiant est inconnu."""
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

    def resolve_objection(self, opportunity_id: str, objection_id: str, resolution: str) -> bool:
        """Clôt une objection traitée. Retourne False si l'identifiant est inconnu.

        Sans ce chemin d'écriture, les objections s'accumulent indéfiniment : le contexte se
        pollue à chaque cycle et le routage par enjeu reste bloqué sur le modèle le plus cher,
        puisqu'il s'appuie sur la présence d'objections ouvertes.
        """
        ...

    def find_opportunity_by_phone(self, phone_number: str) -> str | None:
        """Retrouve une opportunité à partir du numéro d'un interlocuteur.

        Doit être résolu côté CRM (recherche indexée), jamais par balayage du portefeuille :
        c'est appelé à chaque message entrant.
        """
        ...

    def log_call(self, opportunity_id: str, outcome: CallOutcome) -> None:
        """Enregistre un appel téléphonique réel (transcript, durée, issue)."""
        ...


class CatalogPort(Protocol):
    """Fiches produits — prix et caractéristiques que l'agent n'a pas le droit d'inventer."""

    def list_products(self) -> list[Product]: ...

    def get_product(self, product_id: str) -> Product | None: ...
