"""Primary command-line adapter.

Serves three purposes: running a scan by hand (without waiting for the cron), replaying an
event on a specific opportunity, and walking through the demo scenarios.

    python -m revenue_agent.entrypoints.cli scan
    python -m revenue_agent.entrypoints.cli decide acme-co "Julie a répondu : c'est trop cher"
    python -m revenue_agent.entrypoints.cli demo
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from revenue_agent.config import ConfigurationError, Settings
from revenue_agent.container import build_container
from revenue_agent.logging_setup import configure_logging

DEMO_OPPORTUNITY = "acme-co"

DEMO_SCENARIOS: list[tuple[str, str]] = [
    (
        "Signal d'intérêt après deux mois de silence",
        "Julie Martin a rouvert l'email de pricing trois fois cette semaine et a consulté la "
        "page tarifs du site. Aucun message direct reçu.",
    ),
    (
        "Objection prix reçue par email",
        "Julie a répondu par email : « On aime beaucoup le produit mais c'est trop cher pour "
        "nous par rapport à notre budget actuel. »",
    ),
    (
        "Demande dépassant les limites d'autonomie",
        "Julie demande une remise de 40 % sur le prix catalogue pour signer avant la fin du "
        "mois, sur un engagement de trois ans.",
    ),
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="revenue-agent", description="Agent commercial autonome")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("scan", help="Scanner le CRM et traiter les opportunités détectées")

    decide_parser = subparsers.add_parser("decide", help="Rejouer un événement sur une opportunité")
    decide_parser.add_argument("opportunity_id")
    decide_parser.add_argument("event")

    subparsers.add_parser("demo", help="Dérouler les scénarios de démonstration")
    subparsers.add_parser("status", help="Afficher la configuration effective")

    approvals_parser = subparsers.add_parser(
        "approvals", help="Arbitrer les actions en attente de validation humaine"
    )
    approvals_sub = approvals_parser.add_subparsers(dest="approvals_command", required=True)
    approvals_sub.add_parser("list", help="Lister les actions en attente")
    for verb, helptext in (("approve", "Approuver et envoyer"), ("reject", "Rejeter")):
        action_parser = approvals_sub.add_parser(verb, help=helptext)
        action_parser.add_argument("approval_id")
        action_parser.add_argument("--reviewer", default=os.environ.get("USER", "opérateur"))
        action_parser.add_argument("--note", default="")

    args = parser.parse_args(argv)

    try:
        settings = Settings.from_env()
    except ConfigurationError as exc:
        print(f"Configuration invalide : {exc}", file=sys.stderr)
        return 2

    configure_logging(settings.log_level)
    container = build_container(settings)

    if args.command == "scan":
        report = container.scan_for_leads.execute()
        print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
        return 0

    if args.command == "decide":
        result = container.decision_cycle.execute_safely(args.opportunity_id, args.event)
        if result is None:
            print("Cycle de décision en échec — voir les logs.", file=sys.stderr)
            return 1
        _print_decision(result.summary)
        return 0

    if args.command == "demo":
        for title, event in DEMO_SCENARIOS:
            print("\n" + "═" * 78)
            print(f"  {title}")
            print("═" * 78)
            result = container.decision_cycle.execute_safely(DEMO_OPPORTUNITY, event)
            if result is not None:
                print(f"\n  Routage : {result.routing_reason}")
                _print_decision(result.summary)
        return 0

    if args.command == "approvals":
        return _run_approvals(container, args)

    if args.command == "status":
        degraded = settings.degraded_components()
        policy = settings.policy
        print(f"Entreprise         : {settings.company_name}")
        print(f"Mode d'autonomie   : {policy.mode.value}")
        print(f"Seuil d'autonomie  : {policy.max_autonomous_amount:,.0f} €")
        print(f"Cadence max        : {policy.max_outbound_per_day} message(s)/prospect/24 h")
        print(
            "Destinataires      : "
            + (
                ", ".join(policy.allowed_recipients)
                if policy.allowed_recipients
                else "non restreints"
            )
        )
        print(f"Modèle routine     : {settings.openrouter.model_routine}")
        print(f"Modèle stratégie   : {settings.openrouter.model_strategic}")
        print(f"CRM                : {'HubSpot' if settings.hubspot.enabled else 'JSON local'}")
        print(f"Mode simulé        : {' ; '.join(degraded) if degraded else 'aucun'}")
        print(f"En attente de vald.: {len(container.review_approval.list_pending())}")
        return 0

    return 1


def _run_approvals(container, args) -> int:
    review = container.review_approval

    if args.approvals_command == "list":
        pending = review.list_pending()
        if not pending:
            print("Aucune action en attente de validation.")
            return 0
        print(f"{len(pending)} action(s) en attente :\n")
        for approval in pending:
            print(approval.summary())
            print()
        print("Pour arbitrer : approvals approve <id>  |  approvals reject <id> --note '...'")
        return 0

    if args.approvals_command == "approve":
        print(review.approve(args.approval_id, args.reviewer, args.note))
        return 0

    if args.approvals_command == "reject":
        print(review.reject(args.approval_id, args.reviewer, args.note))
        return 0

    return 1


def _print_decision(summary: str) -> None:
    if summary:
        print(f"\n🧠 {summary}\n")


if __name__ == "__main__":
    raise SystemExit(main())
