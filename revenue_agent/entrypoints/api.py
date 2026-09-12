"""Primary HTTP adapter — exposes the use cases to the outside world.

Three families of callers:

* **Trigger.dev** calls `POST /scan` on a cron cadence. That is the heartbeat which makes the
  agent autonomous.
* **Retell** calls `POST /retell/tool-call` during a call (the voice agent executing an action)
  and `POST /retell/webhook` at the end (`call_analyzed` carries the summary and the sentiment).
* **Meta** calls `POST /whatsapp/inbound` when the prospect replies.

No business logic here: validation, authentication, translation from the transport into the use
cases, and back. That is the very definition of an adapter.
"""

from __future__ import annotations

import hmac
import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import BackgroundTasks, FastAPI, Header, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

from revenue_agent.adapters.communication.retell_voice import verify_signature
from revenue_agent.config import Settings
from revenue_agent.container import Container, build_container
from revenue_agent.domain.errors import RevenueAgentError
from revenue_agent.domain.models import CallOutcome
from revenue_agent.entrypoints.approval_page import render as render_approval_page
from revenue_agent.logging_setup import configure_logging

logger = logging.getLogger(__name__)

_container: Container | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _container
    settings = Settings.from_env()
    configure_logging(settings.log_level)
    _container = build_container(settings)
    logger.info("Autonomous Revenue Agent démarré pour %s", settings.company_name)
    yield
    _container = None


app = FastAPI(title="Autonomous Revenue Agent", version="1.0.0", lifespan=lifespan)


def container() -> Container:
    if _container is None:  # pragma: no cover - startup guardrail
        raise RuntimeError("Container non initialisé")
    return _container


# -- Health ------------------------------------------------------------------------


@app.get("/health")
async def health() -> dict:
    current = container()
    settings = current.settings
    return {
        "status": "ok",
        "entreprise": settings.company_name,
        "mode_autonomie": settings.policy.mode.value,
        "seuil_autonomie_eur": settings.policy.max_autonomous_amount,
        "actions_en_attente": len(current.review_approval.list_pending()),
        "composants_simules": settings.degraded_components(),
    }


# -- Human approval ----------------------------------------------------------------


def _approval_access_ok(current: Container, token: str | None) -> bool:
    """The arbitration routes trigger real sends: they cannot be left open on a public URL.

    The token travels as a URL parameter so that a link received in Teams is clickable from a
    phone. This is a deliberate trade-off — a token in a URL ends up in server logs —
    acceptable for a short-lived internal tool, to be replaced by real authentication if the
    tool becomes permanent.
    """
    expected = current.settings.handoff.approval_ui_token
    if not expected:
        logger.warning("APPROVAL_UI_TOKEN non configuré — les routes d'arbitrage sont ouvertes")
        return True
    return bool(token and hmac.compare_digest(token, expected))


@app.get("/approvals")
async def list_approvals(token: str | None = None) -> JSONResponse:
    """Actions held by the policy, awaiting human arbitration.

    As long as an action appears here, nothing has reached the prospect.
    """
    current = container()
    if not _approval_access_ok(current, token):
        return JSONResponse({"detail": "Accès refusé"}, status_code=401)
    pending = current.review_approval.list_pending()
    return JSONResponse(
        [
            {
                "id": approval.id,
                "opportunity": approval.opportunity_id,
                "entreprise": approval.company,
                "canal": approval.kind.value,
                "destinataire": approval.recipient,
                "regle": approval.rule,
                "motif": approval.reason,
                "demandee_le": approval.requested_at.isoformat(),
                "contenu": approval.payload,
            }
            for approval in pending
        ]
    )


@app.get("/approvals/ui", response_class=HTMLResponse)
async def approvals_ui(token: str | None = None) -> Response:
    """Arbitration page: the exact message that will go out, and two buttons.

    This is what takes approval out of the terminal — the recipient of a Teams notification
    opens this link on their phone and decides in two seconds.
    """
    current = container()
    if not _approval_access_ok(current, token):
        return HTMLResponse("<p>Lien d'arbitrage invalide ou expiré.</p>", status_code=401)

    return HTMLResponse(
        render_approval_page(
            current.review_approval.list_pending(),
            token or "",
            current.settings.company_name,
        )
    )


@app.post("/approvals/{approval_id}/approve")
async def approve(approval_id: str, request: Request, token: str | None = None) -> JSONResponse:
    current = container()
    if not _approval_access_ok(current, token):
        return JSONResponse({"detail": "Accès refusé"}, status_code=401)
    body = _safe_json(await request.body())
    result = current.review_approval.approve(
        approval_id, body.get("reviewer", "api"), body.get("note", "")
    )
    return JSONResponse({"resultat": result})


@app.post("/approvals/{approval_id}/reject")
async def reject(approval_id: str, request: Request, token: str | None = None) -> JSONResponse:
    current = container()
    if not _approval_access_ok(current, token):
        return JSONResponse({"detail": "Accès refusé"}, status_code=401)
    body = _safe_json(await request.body())
    result = current.review_approval.reject(
        approval_id, body.get("reviewer", "api"), body.get("note", "")
    )
    return JSONResponse({"resultat": result})


# -- Periodic scan -----------------------------------------------------------------


@app.post("/scan")
async def scan(x_scan_token: str | None = Header(default=None)) -> JSONResponse:
    """Triggered by Trigger.dev. Protected by a shared secret: this endpoint consumes LLM
    tokens, it must not be left open to the public internet."""
    current = container()
    expected = current.settings.scan.shared_secret

    if expected and not (x_scan_token and hmac.compare_digest(x_scan_token, expected)):
        return JSONResponse({"detail": "Jeton de scan invalide"}, status_code=401)
    if not expected:
        logger.warning("SCAN_SHARED_SECRET non configuré — endpoint /scan non protégé")

    report = current.scan_for_leads.execute()
    logger.info(
        "Scan terminé : %s deal(s) examiné(s), %s détecté(s), %s traité(s)",
        report.scanned_deals,
        report.detected,
        len(report.processed),
    )
    return JSONResponse(report.as_dict())


# -- Retell ------------------------------------------------------------------------


@app.post("/retell/tool-call")
async def retell_tool_call(
    request: Request, x_retell_signature: str | None = Header(default=None)
) -> JSONResponse:
    """Executes an action requested by the voice agent during a call.

    Retell's verified format: `{name, args, call}`. The JSON response `{result}` lets Retell
    turn it into a "response variable" usable later in the conversation.
    """
    current = container()
    raw_body = await request.body()

    if not _retell_signature_ok(current, raw_body, x_retell_signature):
        return JSONResponse({"detail": "Signature invalide"}, status_code=401)

    payload = _safe_json(raw_body)
    name = payload.get("name") or payload.get("function_name")
    args = payload.get("args") or payload.get("arguments") or {}

    action = _RETELL_ACTIONS.get(name)
    if action is None:
        logger.warning("Tool inconnu demandé par Retell : %s", name)
        return JSONResponse({"result": f"Action inconnue : {name}"}, status_code=404)

    try:
        result = action(current, args)
    except RevenueAgentError as exc:
        logger.warning("Action %s en échec pendant un appel : %s", name, exc)
        return JSONResponse({"result": f"ERREUR : {exc}"})

    return JSONResponse({"result": result})


@app.post("/retell/webhook")
async def retell_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_retell_signature: str | None = Header(default=None),
) -> JSONResponse:
    """Call events. Only `call_analyzed` carries the summary and the sentiment — so that is
    the one feeding the decision engine, not `call_ended`."""
    current = container()
    raw_body = await request.body()

    if not _retell_signature_ok(current, raw_body, x_retell_signature):
        return JSONResponse({"detail": "Signature invalide"}, status_code=401)

    payload = _safe_json(raw_body)
    event = payload.get("event")
    if event != "call_analyzed":
        return JSONResponse({"status": "ignoré", "event": event})

    call = payload.get("call", {})
    opportunity_id = (call.get("metadata") or {}).get("opportunity_id")
    if not opportunity_id:
        logger.warning("Appel Retell sans opportunity_id dans metadata — ignoré")
        return JSONResponse({"status": "ignoré", "reason": "opportunity_id absent"})

    analysis = call.get("call_analysis") or {}
    outcome = CallOutcome(
        opportunity_id=opportunity_id,
        transcript=call.get("transcript") or "",
        summary=analysis.get("call_summary") or "",
        sentiment=analysis.get("user_sentiment") or "",
        duration_seconds=int((call.get("duration_ms") or 0) / 1000),
        successful=bool(analysis.get("call_successful", True)),
    )

    # Background processing: the decision cycle can take several seconds, and Retell
    # expects a fast acknowledgement.
    background_tasks.add_task(current.handle_call_outcome.execute, outcome)
    return JSONResponse({"status": "accepté", "opportunity": opportunity_id})


# -- WhatsApp ----------------------------------------------------------------------


@app.get("/whatsapp/inbound")
async def whatsapp_verify(request: Request) -> Response:
    """Verification handshake required by Meta when configuring the webhook."""
    params = request.query_params
    expected = container().settings.whatsapp.verify_token
    provided = params.get("hub.verify_token")

    if expected and provided and hmac.compare_digest(provided, expected):
        return PlainTextResponse(params.get("hub.challenge", ""))
    return JSONResponse({"detail": "Jeton de vérification invalide"}, status_code=403)


@app.post("/whatsapp/inbound")
async def whatsapp_inbound(request: Request, background_tasks: BackgroundTasks) -> JSONResponse:
    """An inbound message kicks off a full decision cycle."""
    current = container()
    payload = _safe_json(await request.body())

    message = _extract_whatsapp_message(payload)
    if message is None:
        return JSONResponse({"status": "ignoré"})

    phone, text = message
    opportunity_id = _resolve_opportunity_by_phone(current, phone)
    if opportunity_id is None:
        logger.warning("Message WhatsApp de %s — aucune opportunité correspondante", phone)
        return JSONResponse({"status": "ignoré", "reason": "prospect inconnu"})

    background_tasks.add_task(
        current.decision_cycle.execute_safely,
        opportunity_id,
        f"Message WhatsApp reçu du prospect : « {text} »",
    )
    return JSONResponse({"status": "accepté", "opportunity": opportunity_id})


# -- Helpers -----------------------------------------------------------------------


_RETELL_ACTIONS: dict[str, Any] = {
    "get_opportunity_context": lambda c, a: c.actions.get_opportunity_context(
        a["opportunity_id"]
    ),
    "get_product_info": lambda c, a: c.actions.get_product_info(a.get("product_id", "")),
    "research_prospect": lambda c, a: c.actions.research_prospect(a["company_name"]),
    "record_interaction": lambda c, a: c.actions.record_interaction(
        a["opportunity_id"], a.get("channel", "voice"), a["summary"]
    ),
    "update_opportunity": lambda c, a: c.actions.update_opportunity(
        a["opportunity_id"],
        stage=a.get("stage", ""),
        probability=int(a.get("probability", -1)),
        objection=a.get("objection", ""),
        objection_root_cause=a.get("objection_root_cause", ""),
        next_steps=a.get("next_steps", ""),
    ),
    "update_stakeholder": lambda c, a: c.actions.update_stakeholder(
        a["opportunity_id"],
        a["name"],
        a["stance"],
        a.get("role", ""),
        a.get("notes", ""),
    ),
    "resolve_objection": lambda c, a: c.actions.resolve_objection(
        a["opportunity_id"], a["objection_id"], a.get("resolution", "")
    ),
    "send_email": lambda c, a: c.actions.send_email(
        a["opportunity_id"], a["subject"], a["body"]
    ),
    "schedule_follow_up": lambda c, a: c.actions.schedule_follow_up(
        a["opportunity_id"], a.get("reason", ""), a["due_date"]
    ),
    "escalate_to_human": lambda c, a: c.actions.escalate_to_human(
        a["opportunity_id"],
        a.get("reason", ""),
        a.get("urgency", "normale"),
        a.get("context_brief", ""),
    ),
}


def _retell_signature_ok(current: Container, body: bytes, signature: str | None) -> bool:
    secret = current.settings.retell.webhook_secret
    if not secret:
        logger.warning("RETELL_WEBHOOK_SECRET non configuré — signature non vérifiée")
        return True
    return verify_signature(payload=body, signature=signature, secret=secret)


def _safe_json(body: bytes) -> dict:
    import json

    try:
        payload = json.loads(body or b"{}")
    except ValueError:
        logger.warning("Payload non-JSON reçu")
        return {}
    return payload if isinstance(payload, dict) else {}


def _extract_whatsapp_message(payload: dict) -> tuple[str, str] | None:
    try:
        value = payload["entry"][0]["changes"][0]["value"]
        message = value["messages"][0]
        return message["from"], message["text"]["body"]
    except (KeyError, IndexError, TypeError):
        return None


def _resolve_opportunity_by_phone(current: Container, phone: str) -> str | None:
    """Delegates resolution to the CRM, which has an index on phone numbers.

    The previous version loaded up to a hundred opportunities — one request each — on every
    inbound message, only read the first page, and matched on the last nine digits: slow, a
    quota hog, and capable of attributing a message to the wrong prospect.
    """
    try:
        return current.crm.find_opportunity_by_phone(phone)
    except RevenueAgentError:
        logger.exception("Impossible de résoudre l'opportunité pour le numéro %s", phone)
        return None
