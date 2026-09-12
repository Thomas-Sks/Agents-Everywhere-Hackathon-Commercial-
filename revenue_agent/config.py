"""Application configuration — the single source of truth for the environment.

Loaded once at start-up and validated immediately: a misconfigured deployment must fail at
boot, not on the first customer request.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum


class ConfigurationError(RuntimeError):
    """Invalid configuration — raised at start-up, never mid-request."""


def _load_dotenv() -> None:
    """Loads a `.env` file if one exists, without overriding the existing environment.

    The order of precedence matters: a variable exported in the shell or injected by the
    orchestrator (Trigger.dev, a container) must always win over the local file, otherwise a
    `.env` left lying around on a development machine would contaminate a deployment.
    """
    from dotenv import load_dotenv

    load_dotenv(override=False)


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_float(name: str, default: float) -> float:
    raw = _env(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} doit être un nombre, reçu : {raw!r}") from exc


def _env_list(name: str) -> tuple[str, ...]:
    raw = _env(name)
    return tuple(item.strip() for item in raw.split(",") if item.strip()) if raw else ()


def _parse_mode(raw: str):
    try:
        return AgentMode(raw.lower())
    except ValueError as exc:
        valid = ", ".join(mode.value for mode in AgentMode)
        raise ConfigurationError(f"AGENT_MODE invalide : {raw!r}. Valeurs : {valid}") from exc


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} doit être un entier, reçu : {raw!r}") from exc


@dataclass(frozen=True)
class OpenRouterSettings:
    api_key: str
    base_url: str = "https://openrouter.ai/api/v1"
    model_routine: str = "openai/gpt-5.6-luna"
    model_strategic: str = "openai/gpt-5.6-sol"
    fallback_models: tuple[str, ...] = ("openai/gpt-5.6-terra",)

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)


@dataclass(frozen=True)
class HubSpotSettings:
    token: str

    @property
    def enabled(self) -> bool:
        return bool(self.token)


@dataclass(frozen=True)
class ResendSettings:
    api_key: str
    from_address: str = "agent@resend.dev"

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)


@dataclass(frozen=True)
class WhatsAppSettings:
    token: str
    phone_number_id: str
    verify_token: str = ""

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.phone_number_id)


@dataclass(frozen=True)
class RetellSettings:
    api_key: str
    from_number: str = ""
    agent_id: str = ""
    webhook_secret: str = ""

    @property
    def enabled(self) -> bool:
        return bool(self.api_key and self.from_number and self.agent_id)


@dataclass(frozen=True)
class HandoffSettings:
    """Where to reach a human.

    `public_base_url` is the public URL of this API (an ngrok tunnel in a demo): it is used to
    build the arbitration link sent in notifications. Without it, a recipient gets the
    information but has no way to act on it.
    """

    teams_webhook_url: str = ""
    whatsapp_recipients: tuple[str, ...] = ()
    hubspot_owner_id: str = ""
    public_base_url: str = ""
    approval_ui_token: str = ""

    @property
    def teams_enabled(self) -> bool:
        return bool(self.teams_webhook_url)

    @property
    def whatsapp_enabled(self) -> bool:
        return bool(self.whatsapp_recipients)

    def approval_url(self, approval_id: str = "") -> str:
        if not self.public_base_url or not self.approval_ui_token:
            return ""
        base = f"{self.public_base_url.rstrip('/')}/approvals/ui?token={self.approval_ui_token}"
        return f"{base}#{approval_id}" if approval_id else base


@dataclass(frozen=True)
class ExaSettings:
    api_key: str

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)


@dataclass(frozen=True)
class ScanSettings:
    """Settings for the periodic CRM scan.

    `overlap_minutes` compensates for the *eventual consistency* of HubSpot's search index: we
    systematically re-read a little before the last pointer, and dedupe by id.
    `max_decisions_per_scan` bounds the LLM cost of a single tick: triage may surface 200 leads,
    but we only process a number known in advance.
    """

    overlap_minutes: int = 5
    inactivity_days: int = 14
    max_decisions_per_scan: int = 5
    page_size: int = 100
    shared_secret: str = ""


class AgentMode(StrEnum):
    """The level of autonomy granted to the agent.

    The default is `SUPERVISED`, and that is deliberate: a system that writes to real prospects
    and places real calls must ship locked down, with autonomy granted explicitly. The opposite
    — shipping autonomous and relying on the operator to restrict it — makes the prospect bear
    the cost of a mistake.
    """

    DRY_RUN = "dry_run"
    SUPERVISED = "supervised"
    AUTONOMOUS = "autonomous"


@dataclass(frozen=True)
class PolicySettings:
    """Autonomy limits **enforced by code**.

    The prompt describes these limits to the model; this policy enforces them. A prompt is an
    instruction, not an access control: nothing guarantees a model will honour it, and nothing
    should depend on its goodwill to stop an email from going out.
    """

    mode: AgentMode = AgentMode.SUPERVISED
    max_autonomous_amount: float = 50_000.0
    allowed_recipients: tuple[str, ...] = ()
    max_outbound_per_day: int = 3

    @property
    def blocks_everything(self) -> bool:
        return self.mode is AgentMode.DRY_RUN


@dataclass(frozen=True)
class Settings:
    company_name: str
    openrouter: OpenRouterSettings
    hubspot: HubSpotSettings
    resend: ResendSettings
    whatsapp: WhatsAppSettings
    retell: RetellSettings
    exa: ExaSettings
    handoff: HandoffSettings
    scan: ScanSettings
    policy: PolicySettings
    state_file: str
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> Settings:
        _load_dotenv()
        settings = cls(
            company_name=_env("COMPANY_NAME", "Notre Entreprise"),
            openrouter=OpenRouterSettings(
                api_key=_env("OPENROUTER_API_KEY"),
                base_url=_env("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
                model_routine=_env("MODEL_ROUTINE", "openai/gpt-5.6-luna"),
                model_strategic=_env("MODEL_STRATEGIC", "openai/gpt-5.6-sol"),
            ),
            hubspot=HubSpotSettings(token=_env("HUBSPOT_TOKEN")),
            resend=ResendSettings(
                api_key=_env("RESEND_API_KEY"),
                from_address=_env("RESEND_FROM_ADDRESS", "agent@resend.dev"),
            ),
            whatsapp=WhatsAppSettings(
                token=_env("WHATSAPP_TOKEN"),
                phone_number_id=_env("WHATSAPP_PHONE_NUMBER_ID"),
                verify_token=_env("WHATSAPP_VERIFY_TOKEN"),
            ),
            retell=RetellSettings(
                api_key=_env("RETELL_API_KEY"),
                from_number=_env("RETELL_FROM_NUMBER"),
                agent_id=_env("RETELL_AGENT_ID"),
                webhook_secret=_env("RETELL_WEBHOOK_SECRET"),
            ),
            exa=ExaSettings(api_key=_env("EXA_API_KEY")),
            handoff=HandoffSettings(
                teams_webhook_url=_env("TEAMS_WEBHOOK_URL"),
                whatsapp_recipients=_env_list("HANDOFF_WHATSAPP_NUMBERS"),
                hubspot_owner_id=_env("HUBSPOT_OWNER_ID"),
                public_base_url=_env("PUBLIC_BASE_URL"),
                approval_ui_token=_env("APPROVAL_UI_TOKEN"),
            ),
            scan=ScanSettings(
                overlap_minutes=_env_int("SCAN_OVERLAP_MINUTES", 5),
                inactivity_days=_env_int("SCAN_INACTIVITY_DAYS", 14),
                max_decisions_per_scan=_env_int("SCAN_MAX_DECISIONS", 5),
                page_size=_env_int("SCAN_PAGE_SIZE", 100),
                shared_secret=_env("SCAN_SHARED_SECRET"),
            ),
            policy=PolicySettings(
                mode=_parse_mode(_env("AGENT_MODE", AgentMode.SUPERVISED.value)),
                max_autonomous_amount=_env_float("MAX_AUTONOMOUS_AMOUNT", 50_000.0),
                allowed_recipients=_env_list("ALLOWED_RECIPIENTS"),
                max_outbound_per_day=_env_int("MAX_OUTBOUND_PER_DAY", 3),
            ),
            state_file=_env("STATE_FILE", "var/state.json"),
            log_level=_env("LOG_LEVEL", "INFO"),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if self.scan.overlap_minutes < 0:
            raise ConfigurationError("SCAN_OVERLAP_MINUTES ne peut pas être négatif")
        if self.scan.max_decisions_per_scan < 1:
            raise ConfigurationError("SCAN_MAX_DECISIONS doit valoir au moins 1")
        if not 1 <= self.scan.page_size <= 200:
            raise ConfigurationError("SCAN_PAGE_SIZE doit être compris entre 1 et 200")

    def degraded_components(self) -> list[str]:
        """Components running in simulated mode for lack of configuration — displayed at
        start-up so a demo never believes itself wired up when it is not."""
        degraded = []
        if not self.openrouter.enabled:
            degraded.append("LLM (agent scripté, aucune décision réelle)")
        if not self.hubspot.enabled:
            degraded.append("CRM (mémoire locale JSON)")
        if not self.resend.enabled:
            degraded.append("email (console)")
        if not self.whatsapp.enabled:
            degraded.append("WhatsApp (console)")
        if not self.retell.enabled:
            degraded.append("voix (console)")
        if not self.exa.enabled:
            degraded.append("enrichissement web (désactivé)")
        if (
            not self.handoff.teams_enabled
            and not self.hubspot.enabled
            and not (self.handoff.whatsapp_enabled and self.whatsapp.enabled)
        ):
            degraded.append("handoff (console — AUCUN humain n'est réellement prévenu)")
        return degraded
