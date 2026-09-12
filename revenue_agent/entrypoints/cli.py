"""Primary command-line adapter.

Serves three purposes: running a scan by hand (without waiting for the cron), replaying an
event on a specific opportunity, and walking through the demo scenarios.

    python -m revenue_agent.entrypoints.cli scan
    python -m revenue_agent.entrypoints.cli decide acme-co "Julie replied: it is too expensive"
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
        "Buying signal after two months of silence",
        "Julie Martin reopened the pricing email three times this week and visited the "
        "pricing page. No direct message received.",
    ),
    (
        "Price objection received by email",
        "Julie replied by email: \"We really like the product but it is too expensive for "
        "us against our current budget.\"",
    ),
    (
        "Request beyond the autonomy limits",
        "Julie is asking for a 40% discount off list price to sign before the end of the "
        "month, on a three-year commitment.",
    ),
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="revenue-agent", description="Autonomous sales agent")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("scan", help="Scan the CRM and process detected opportunities")

    decide_parser = subparsers.add_parser("decide", help="Replay an event on an opportunity")
    decide_parser.add_argument("opportunity_id")
    decide_parser.add_argument("event")

    subparsers.add_parser("demo", help="Run the demonstration scenarios")
    subparsers.add_parser("status", help="Show the effective configuration")

    approvals_parser = subparsers.add_parser(
        "approvals", help="Arbitrate actions awaiting human approval"
    )
    approvals_sub = approvals_parser.add_subparsers(dest="approvals_command", required=True)
    approvals_sub.add_parser("list", help="List pending actions")
    for verb, helptext in (("approve", "Approve and send"), ("reject", "Reject")):
        action_parser = approvals_sub.add_parser(verb, help=helptext)
        action_parser.add_argument("approval_id")
        action_parser.add_argument("--reviewer", default=os.environ.get("USER", "operator"))
        action_parser.add_argument("--note", default="")

    args = parser.parse_args(argv)

    try:
        settings = Settings.from_env()
    except ConfigurationError as exc:
        print(f"Invalid configuration: {exc}", file=sys.stderr)
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
            print("Decision cycle failed — see the logs.", file=sys.stderr)
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
                print(f"\n  Routing: {result.routing_reason}")
                _print_decision(result.summary)
        return 0

    if args.command == "approvals":
        return _run_approvals(container, args)

    if args.command == "status":
        degraded = settings.degraded_components()
        policy = settings.policy
        print(f"Company            : {settings.company_name}")
        print(f"Autonomy mode      : {policy.mode.value}")
        print(f"Autonomy threshold : {policy.max_autonomous_amount:,.0f} EUR")
        print(f"Max cadence        : {policy.max_outbound_per_day} message(s)/prospect/24h")
        print(
            "Recipients         : "
            + (
                ", ".join(policy.allowed_recipients)
                if policy.allowed_recipients
                else "unrestricted"
            )
        )
        print(f"Routine model      : {settings.openrouter.model_routine}")
        print(f"Strategic model    : {settings.openrouter.model_strategic}")
        print(f"CRM                : {'HubSpot' if settings.hubspot.enabled else 'local JSON'}")
        print(f"Simulated          : {' ; '.join(degraded) if degraded else 'none'}")
        print(f"Pending approvals  : {len(container.review_approval.list_pending())}")
        return 0

    return 1


def _run_approvals(container, args) -> int:
    review = container.review_approval

    if args.approvals_command == "list":
        pending = review.list_pending()
        if not pending:
            print("No action awaiting approval.")
            return 0
        print(f"{len(pending)} action(s) pending:\n")
        for approval in pending:
            print(approval.summary())
            print()
        print("To arbitrate: approvals approve <id>  |  approvals reject <id> --note '...'")
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
