# Autonomous Revenue Agent

**A digital sales rep that lives inside your CRM, not in a chat window.**

It doesn't generate emails. It scans the book of business, spots the opportunities dying in
silence, **decides** on the best commercial action — email, WhatsApp, phone call, deliberate
waiting, human escalation — carries it out, then writes everything it learns back into the CRM.

> The channel is not the product. **The commercial decision is the product.**

> 🎥 **Demo:** the video walkthrough is part of our submission on the hackathon site.

---

## The problem

No sales rep loses a deal because they write bad emails. They lose it because nobody reopens an
opportunity that has been asleep for two months.

A CRM is a passive system: it **records** what happened, it does nothing. Dormant deals send no
notification — that is precisely their problem. There is no such event as "this prospect has
been forgetting about you for 62 days." And the day a rep finally picks the file back up, the
context is gone: why the silence? Was it the price, or the contractual constraint mentioned on a
call back in June?

A conventional AI assistant solves none of this, for a structural reason: **you have to go to
it.** It waits to be spoken to. But here, nobody is speaking. That is exactly the problem.

## What the agent actually does

Here is a real cycle — the one that fires on the demo's first `scan`:

```
Trigger.dev (cron, 5 min) ──> POST /scan
  │
  ├─ 1. HubSpot: deals modified since (last pointer − 5 min overlap window)
  ├─ 2. Deterministic triage, zero LLM calls:
  │      new lead · stage change · follow-up due · prolonged inactivity
  │      → "Acme Co: no action for 62 days"
  ├─ 3. Stakes assessment: €68,000 → strategic reasoning (capable model)
  ├─ 4. The decision engine reads the full file and understands the silence
  │      is not disinterest: a June objection said "we can't switch before
  │      our current contract ends"
  ├─ 5. It notes the budget holder was never engaged — and writes that onto
  │      the stakeholder map — looks up recent news about Acme (Exa), and drafts
  ├─ 6. Policy + pre-send review → approval queue or direct send
  └─ 7. Written trace in HubSpot: what isn't written there didn't happen
```

And when the prospect replies — over WhatsApp, or by picking up the phone — **the same cycle
resumes at the same point**, with the same memory. A prospect never has to start their story
over just because the channel changed.

## Why this cannot be a chatbot

Three properties of the product disappear the moment you take it out of its environment.

**1. The trigger is an absence, not a message.** The agent's four triggers (new lead, stage
change, follow-up due, inactivity) are events *nobody emits*. Two of them are literally
non-events: they arise from the fact that nothing happened. No chat window can produce that —
there is no one there to type "this deal is dying."

**2. The CRM is the long-term memory, and it is shared.** Everything the agent learns is written
into HubSpot as prefixed notes: readable by a human in the UI, re-parsable by the agent on the
next scan. The agent and the rep therefore work on **the same brain**, not on two diverging
histories. There is no hidden database alongside the CRM that reps would never see.

**3. The text agent and the voice agent are the same agent.** During a phone call, when the
voice agent looks up a product sheet or records an objection, it calls — over a webhook —
**exactly the same action registry** the written engine uses. Not two implementations doomed to
diverge: one codebase, two modalities. The call ends, `call_analyzed` returns the transcript, and
a fresh decision cycle starts from what was actually said.

## What the agent knows about selling

Guardrails and architecture are what make it safe. This is what makes it a *sales* agent rather
than a scheduler that sends templates.

**An objection has a lifecycle, not just an existence.** When the agent detects one, it records
the wording *and the cause it suspects* — "too expensive" can mean no budget, misunderstood
value, the wrong contact, or simply a wish to end the conversation, and those lead to opposite
next moves. When the objection is lifted, the agent closes it with `resolve_objection`. That
second half is not bookkeeping: an objection left open forever keeps reading as an active
blocker, pollutes every later cycle, and — since stakes routing keys off open objections — pins
the deal on the expensive model for good.

**The map of who decides is written, not just read.** A complex sale rarely fails on the product.
The agent maintains who champions, who funds, who decides, who blocks, with `update_stakeholder`
— **including people it has never contacted**. The CFO who signs off the budget belongs on the
map even without an email address; leaving them off is exactly what makes a deal look healthy
right up until it dies. A stance observed but not written down is lost by the next cycle.

**A price is never quoted from memory.** Every figure goes through `get_product_info`, and the
policy blocks any amount absent from the catalogue before it can leave. Asking the model to check
is not the same as guaranteeing it.

**Waiting is a first-class action.** `schedule_follow_up` is a decision with a reason and a date,
not an absence of one — and the periodic scan is what makes that promise real months later.

## The environment shapes the code

These are not integration details: each of these constraints changed a design decision. That is
what separates an agent *inside* an environment from an agent *next to* one.

| Real environment constraint | What the code does about it |
|---|---|
| HubSpot's search index is *eventually consistent* | A 5-minute overlap window on every scan plus deduplication by id — otherwise leads slip through the cracks |
| HubSpot's Search API is capped at 4 req/s, shared | A 5-minute scan cadence — scanning more often would surface nothing extra |
| HubSpot webhooks require a public app | Polling isn't a stopgap, it's the only mechanism available: so it is designed for, scan pointer included |
| A CRM outage must not lose a window | The pointer **only advances if the CRM answered**, otherwise the window is replayed |
| On day 1, the CRM already holds 500 deals | The first scan is an *inventory*, not a delta: we adopt the book of business without pestering active deals, while dormant ones become eligible immediately |
| `retell_llm_dynamic_variables` only accepts strings | The opportunity context is explicitly flattened before the call |
| It's `call_analyzed`, not `call_ended`, that carries the summary | That is the event feeding the post-call decision |
| Meta returns the number without a `+`, CRMs store it half a dozen ways | Inbound resolution queries the CRM's index across plausible spellings rather than matching on trailing digits, which would attribute a message to the wrong prospect |
| A CRM activity spike must not blow up the bill | A per-scan decision cap, with the surplus deferred to the next tick — never lost |
| HubSpot has no standard field for "who blocks this deal" | The stakeholder map is written as prefixed notes — readable by the rep, re-parsable by the agent, nothing to configure in the customer's portal |

## Operator control

An agent that writes to real prospects and places real calls cannot rest on a model's goodwill.
**A prompt is an instruction; an access control is a guarantee.** Autonomy limits are therefore
enforced in code (`domain/policy.py`), evaluated **before** any outbound action — and
`escalate_to_human` cannot be the only protection, since it is an action the model *chooses* to
call.

| Rule | Verdict | What it prevents |
|---|---|---|
| `dry_run_mode` | blocked | Any send, during rehearsal or testing |
| `recipient_not_allowed` | blocked | Writing to a real prospect during a demo |
| `price_not_in_catalogue` | blocked | A hallucinated price committing the company |
| `rate_limit` | blocked | Hounding a prospect |
| `supervised_mode` | approval | Every action, until autonomy is explicitly granted |
| `review_*` | approval | A price commitment or unbackable promise going out alone |
| `high_amount` | approval | A large deal playing out without supervision |

Three deliberate positions:

**The default is `supervised`.** The system ships locked down and autonomy is granted
explicitly. The reverse would make the prospect bear the cost of the mistake.

**A refusal is returned to the model as text, not as an exception.** The agent can then adapt:
rewrite, switch channel, escalate. The prompt does however forbid it from working around the
filter by rephrasing — dropping the word "discount" while offering the same concession would be
a serious fault.

**A human approval lifts approval requirements, never blocks.** An operator arbitrates
commercial judgement; they do not switch off simulation mode or the allow-list.

### Pre-send review, in two layers

Even in autonomous mode, every message is reviewed before it goes out.

The **lexical baseline** is deterministic, free, instant and always on: it catches what is named
outright ("discount", "-20%", "free of charge"). It cannot see what is merely phrased.

The **semantic review** reads the message the way a sales director would, with the deal stage,
the amount and the open objections — because "we can work something out on price" is harmless at
first contact and serious late in a negotiation. It catches what no dictionary will ever cover:
*"I'll match their rate"*, *"I guarantee you ROI within six months"*, *"the offer expires
tonight"*.

The two layers stack, they do not replace each other: a probabilistic guarantee does not replace
a deterministic one. And **the review fails closed** — if the model is unavailable or answers
badly, the message is held for a human. A reviewer who is absent does not mean a message that
was approved.

```bash
revenue-agent approvals list             # what awaits arbitration, exact message included
revenue-agent approvals approve <id>     # goes out as-is, word for word
revenue-agent approvals reject <id> --note "too early"
```

### Without a terminal

The command line is not the arbitration surface a sales rep expects. When the agent holds an
action back, a human is notified **immediately** — a HubSpot task assigned to the deal owner, a
Teams card, a WhatsApp message to the rep, or all three — with a link to an arbitration page:
the exact message that will go out, and two buttons. Usable from a phone.

The three destinations are complementary rather than redundant. The HubSpot task is durable but
passive: it waits for someone to open the CRM. Teams lands where the team works. WhatsApp lands
in a pocket — which, for an approval that needs an answer within the hour, is often the only one
that gets a reply.

Same for `escalate_to_human`: the full brief lands in the rep's task queue, associated with the
deal. If every destination fails, the brief is dumped into the logs rather than lost — **a
handoff that reaches nobody is a deal abandoned in silence**.

```bash
export TEAMS_WEBHOOK_URL=...      # Power Automate workflow, no OAuth
export HANDOFF_WHATSAPP_NUMBERS=+33600000000   # reuses the Meta connection already set up
export HUBSPOT_OWNER_ID=...       # fallback owner
export PUBLIC_BASE_URL=https://your-tunnel.ngrok.app
export APPROVAL_UI_TOKEN=...      # protects the page: it triggers real sends
```

The human approves **what will actually go out**, never a summary. As long as an action sits in
that queue, nothing has reached the prospect — and a rejection leaves its trace in the CRM, just
as a send does.

---

## Installation and running

**Requirements:** Python 3.11 or later. Nothing else — no database, no Docker, no external
account.

```bash
git clone <repo-url> && cd Agents-Everywhere-Hackathon-Commercial-
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # every variable is commented and optional
```

The `.env` file is loaded automatically, and never overrides a variable already exported in the
shell: a value forgotten locally cannot contaminate a deployment.

### Running with no keys at all (recommended for a first look)

The project runs **immediately, without a single API key**. Every external integration has a
simulated adapter, and the code path exercised is exactly the production one: only the
destination changes (a log line instead of a real send).

```bash
python -m revenue_agent.entrypoints.cli status   # what's wired, what's simulated
python -m revenue_agent.entrypoints.cli scan     # CRM scan + decision cycle
python -m revenue_agent.entrypoints.cli demo     # 3 sales scenarios
python -m revenue_agent.entrypoints.cli approvals list   # the approval queue
```

The first `scan` seeds a local demo CRM, adopts the book of business, spots the opportunity
dormant for 62 days and fires a decision cycle on it.

Without `OPENROUTER_API_KEY`, the whole mechanism still runs — scan, triage, routing, CRM
tracing, approval queue — but the agent *decides* nothing: it records the event and says so
explicitly. That is deliberate: a fallback agent that faked plausible decisions would be worse
than one that announces its own incapacity.

### The one key that changes everything

```bash
export OPENROUTER_API_KEY=sk-or-v1-...
python -m revenue_agent.entrypoints.cli scan
```

**This is the only key needed to watch the agent reason and decide for real.** It activates the
LangGraph decision engine and the semantic message review. Everything else (CRM, email, WhatsApp,
voice, web search) keeps running simulated, without breaking anything.

Get one at [openrouter.ai/keys](https://openrouter.ai/keys) — a single key gives access to every
model, with no separate account per provider.

### The keys, one by one

| Variable | Required? | What it activates | Without it |
|---|---|---|---|
| `OPENROUTER_API_KEY` | **The only one that matters** | Reasoning and semantic review | The agent records events without deciding |
| `HUBSPOT_TOKEN` | optional | Real CRM (deals, contacts, notes, calls) | Pre-seeded local JSON CRM |
| `EXA_API_KEY` | optional | Web search on prospect news | The agent knows it lacks that capability |
| `RESEND_API_KEY` | optional | Real email sending | Email written to the console |
| `WHATSAPP_TOKEN` + `WHATSAPP_PHONE_NUMBER_ID` | optional | Real WhatsApp via Meta Cloud API | Message written to the console |
| `RETELL_API_KEY` + `RETELL_FROM_NUMBER` + `RETELL_AGENT_ID` | optional | Real phone calls | Call written to the console |
| `SCAN_SHARED_SECRET` | if server is exposed | Protects `/scan`, which burns tokens | Endpoint unprotected (warned at startup) |
| `RETELL_WEBHOOK_SECRET` | if server is exposed | Verifies Retell webhook signatures | Signature unverified (warned at startup) |
| `WHATSAPP_VERIFY_TOKEN` | if real WhatsApp | Verification handshake required by Meta | The Meta webhook cannot be registered |

Where to get them: [openrouter.ai](https://openrouter.ai/keys) ·
[HubSpot Private App](https://developers.hubspot.com) (scopes `crm.objects.deals.read/write`,
`crm.objects.contacts.read`, `crm.objects.notes.write`) · [exa.ai](https://exa.ai) ·
[resend.com](https://resend.com) · [Meta Cloud API](https://developers.facebook.com) (free test
mode, 5 numbers) · [retellai.com](https://retellai.com).

`status` shows at any moment what is real and what is simulated: a demo must never believe it is
wired up when it isn't.

### Tuning autonomy

The agent starts **locked down**. These variables decide what it may do on its own.

```bash
export AGENT_MODE=supervised            # default — it drafts, nothing goes out unapproved
export AGENT_MODE=autonomous            # acts alone, except discount / large deal / unknown price
export AGENT_MODE=dry_run               # nothing goes out, even once approved

export MAX_AUTONOMOUS_AMOUNT=50000      # above this, approval is mandatory
export MAX_OUTBOUND_PER_DAY=3           # maximum cadence per prospect
export ALLOWED_RECIPIENTS=you@example.test,+33600000000   # allow-list
```

> ⚠️ **Before wiring a real sending key** (`RESEND_API_KEY`, `WHATSAPP_TOKEN`,
> `RETELL_API_KEY`), set `ALLOWED_RECIPIENTS` to your own contact details. This is the guardrail
> that stops a message from reaching a real prospect during a demo, and no human approval can
> bypass it.

Other settings: `COMPANY_NAME`, `MODEL_ROUTINE`, `MODEL_STRATEGIC`, `SCAN_INACTIVITY_DAYS`
(default 14), `SCAN_MAX_DECISIONS` (default 5), `SCAN_OVERLAP_MINUTES` (default 5), `STATE_FILE`,
`LOG_LEVEL`, `LOG_FORMAT=json`.

## Server

```bash
uvicorn revenue_agent.entrypoints.api:app --port 8000
ngrok http 8000     # so Trigger.dev and Retell can reach us
```

| Endpoint | Caller | Role |
|---|---|---|
| `POST /scan` | Trigger.dev (cron) | Scans the CRM, triages, decides. Protected by `X-Scan-Token` |
| `POST /retell/tool-call` | Retell, mid-call | The voice agent runs an action from the registry |
| `POST /retell/webhook` | Retell, call end | `call_analyzed` → transcript and summary fed back into the decision |
| `POST /whatsapp/inbound` | Meta | An inbound message restarts a full decision cycle |
| `GET /whatsapp/inbound` | Meta | Verification handshake, required to register the webhook |
| `GET /approvals/ui?token=` | operator | **Arbitration page**: exact message and two buttons |
| `GET /approvals?token=` | operator | The same actions as JSON |
| `POST /approvals/{id}/approve` · `/reject` | operator | Arbitration |
| `GET /health` | monitoring | Autonomy mode, pending actions, simulated components |

---

## Architecture

Hexagonal (ports & adapters), and not for the pattern's sake: it is what makes graceful
degradation possible **without a single `if` in the business code**.

```
        entrypoints (FastAPI, CLI)          ← driving adapters
                    │
              application                    ← use cases: scan, decision, call, arbitration
                    │
                 domain                      ← model, triage, stakes, policy (zero dependencies)
                    │
                  ports                      ← interfaces
                    │
   HubSpot · Resend · Meta · Retell · Exa · OpenRouter · local JSON
```

```
revenue_agent/
├── domain/        # model, triage, stakes, autonomy policy, review — zero dependencies
├── ports/         # interfaces (CRM, communication, intelligence, state, approvals)
├── application/   # use cases: scan, decision cycle, call outcome, human arbitration
├── adapters/      # HubSpot, Resend, Meta, Retell, Exa, OpenRouter, local JSON
├── entrypoints/   # FastAPI API, CLI
└── prompts/       # decision engine, message review
trigger/           # Trigger.dev tasks (cron + durable waitpoints)
```

Three concrete benefits:

- **Degradation is a wiring choice.** `container.py` picks `HubSpotCrmAdapter` or
  `JsonFileCrmAdapter`, `ResendEmailAdapter` or `ConsoleEmailAdapter`. The domain is unaware of
  the trade-off.
- **Tests run without a network.** That is the objective measure of the decoupling.
- **Swapping a provider is one file.** HubSpot → Salesforce, Retell → Vapi: one adapter, zero
  impact on the domain.

### What the agent can do

Twelve tools, defined once in `application/action_registry.py` and exposed two ways: as LangGraph
tools to the written engine, and over a webhook to the voice agent mid-call. One implementation,
two modalities — not two copies doomed to diverge.

| Understand | Record | Act |
|---|---|---|
| `get_opportunity_context` | `record_interaction` | `send_email` |
| `get_product_info` | `update_opportunity` | `send_whatsapp_message` |
| `research_prospect` | `update_stakeholder` | `place_phone_call` |
| | `resolve_objection` | `schedule_follow_up` |
| | | `escalate_to_human` |

Every acting tool resolves its own recipient from the CRM — the model never supplies an address
or a phone number — and passes through the policy before anything leaves.

## Stack

| Component | Choice |
|---|---|
| Reasoning | **GPT-5.6** via **OpenRouter** — `luna` for routine, `sol` when the stakes justify it |
| Agent orchestration | **LangGraph** (`langchain.agents.create_agent`), one thread per opportunity, 12 tools |
| Long-running orchestration | **Trigger.dev** — scan cron, durable waitpoints for follow-ups |
| CRM | **HubSpot** (v3/v4 API, notes as shared memory), local JSON fallback |
| Prospect research | **Exa** (bounded time window: a 2019 article is not a buying signal) |
| Voice | **Retell AI** — outbound calls, mid-call tool calls, `call_analyzed` webhook |
| Email / WhatsApp | **Resend** / **Meta Cloud API** |
| API / CLI | **FastAPI**, **httpx**, **argparse** |
| Quality | **pytest**, **ruff** |

### Reasoning is routed by the stakes

If the commercial decision is the product, then *how much reasoning that decision deserves* is
itself a decision. `domain/stakes.py` settles it, as a pure and auditable function:

| Situation | Model |
|---|---|
| Amount ≥ €50k, late stage, unresolved objection, stage change | the capable model |
| Everything else | the economy tier |

Every cycle logs its routing rationale — the cost stays explainable after the fact. And the
triage that precedes it is **deterministic and free**: a scan can surface hundreds of deals, and
having a model reason about each one would cost a lot to conclude, most of the time, that there
is nothing to do.

## Reliability

What has been handled explicitly, because an autonomous loop runs with nobody watching the
screen:

- The scan pointer does not advance if the CRM is unavailable
- A failing opportunity never interrupts the processing of the others
- Adapter errors are returned to the model as text — it switches channel or escalates instead of
  crashing
- HTTP retries on transient statuses only, honouring `Retry-After`; a business 4xx is never
  replayed
- Atomic JSON writes (`os.replace` + `fsync`) under a lock: an interruption leaves no truncated
  state
- An adapter **raises** when it fails; degradation is an application-level decision, taken in the
  use cases

## Tests

```bash
pip install -e ".[dev]"
pytest          # 115 tests
ruff check .
```

**115 tests, no network, no model calls, 0.8 seconds.** They cover triage, stakes routing,
channel resolution, the scan loop, the autonomy policy, message review and the stakeholder
map. The tests in
`test_policy.py` are the most important in the repo: they are the ones verifying what stops a
message from going out.

## Costs

| Item | Cost |
|---|---|
| One decision cycle, economy tier | ~$0.001–0.003 |
| One decision cycle, capable model | ~$0.008–0.02 |
| One pre-send message review | ~$0.0003 |
| One Exa search | ~$0.007 |
| One 5-minute Retell phone call | ~$0.65 |

The LLM is never the dominant cost: **voice is, by a factor of 30 to 600.** That is what
justifies the agent reserving the phone for moments where it adds more than writing — and the
deterministic triage guarantees we only pay for reasoning on the opportunities that deserve it.

---

## A note on language

Everything is in English — documentation, code comments, tests, the agent's prompts, and every
runtime string (logs, CLI output, the arbitration page, the messages the agent returns to the
model). The lexical review patterns were translated along with the prompts: leaving them in
French while the agent writes English would have silently disabled that guardrail.
