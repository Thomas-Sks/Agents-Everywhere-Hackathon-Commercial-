# Architecture

## The thesis

This product is neither an email generator nor a voicebot: it is a **sales decision engine**.
Email, WhatsApp and phone are merely ways of executing a decision. The code is organised so
that this claim holds technically, not just in the pitch: the sales logic knows nothing about
any vendor.

## Hexagonal, and why it matters here

```
        entrypoints (FastAPI, CLI)          ← primary adapters
                    │
              application                    ← use cases
                    │
                 domain                      ← model, triage, stakes, policy (zero dependencies)
                    │
                  ports                      ← interfaces
                    │
   HubSpot · Resend · Meta · Retell · Exa · OpenRouter · local JSON
                                             ← secondary adapters
```

Three concrete benefits, not theoretical ones:

**Degradation becomes a wiring decision.** Previously, "it has to work without an API key"
translated into `if not api_key: print(...)` scattered across every function. Now the
composition root (`container.py`) picks either `HubSpotCrmAdapter` or `JsonFileCrmAdapter`,
either `ResendEmailAdapter` or `ConsoleEmailAdapter`. The domain is unaware of the arbitration,
and the code path exercised in a demo is the same one that runs in production.

**Tests run without a network.** 102 tests cover triage, stakes-based routing, channel
resolution, the scan loop, the autonomy policy and message review — in under a second, without
HubSpot, without an LLM. That is the objective measure that the business logic really is
decoupled.

**Switching vendors is one file.** Moving from HubSpot to Salesforce, from Retell to Vapi, from
OpenRouter to the OpenAI API directly: one adapter, zero impact on the domain.

## The autonomous loop

```
Trigger.dev (cron 5 min) ──> POST /scan
   1. HubSpot search: deals modified since (pointer − 5 min overlap)
   2. dedupe by id               ← the HubSpot search index is eventually consistent
   3. deterministic triage, zero LLM:
        new lead · stage change · follow-up due · prolonged inactivity
   4. per-scan cap               ← a spike of CRM activity must not blow up the bill
   5. decision cycle on the selected opportunities
```

Four design decisions deserve to be spelled out:

**The first scan is an inventory, not a delta.** Without that, an opportunity never seen before
and not recently modified would stay invisible forever — and dormant deals are precisely the
ones worth money. On the first pass, the agent adopts the existing book of business: an active
deal triggers nothing (you do not re-engage 500 prospects on installation day), while a deal
dormant beyond the threshold is immediately eligible.

**The pointer only advances if the CRM answered.** Otherwise a HubSpot outage would make an
entire window look processed, and the leads in that window would be silently lost.

**Triage comes before reasoning.** A scan can surface hundreds of deals; having a model reason
over each one would cost a fortune to conclude, most of the time, that there is nothing to do.
Triage is a pure, free function.

**Polling is not a choice.** HubSpot webhooks require a public app and are not available on a
free developer account. The 5-minute cadence is dictated by the Search API, capped at 4
requests/second shared across objects.

## Stakes-based routing

If the sales decision is the product, then *how much reasoning that decision deserves* is
itself a decision. `domain/stakes.py` settles it, as a pure and auditable function:

| Situation | Model |
|---|---|
| Amount ≥ €50k, late stage, unresolved objection, stage change | `openai/gpt-5.6-sol` |
| Everything else | `openai/gpt-5.6-luna` |

All three identifiers were verified against OpenRouter's public catalogue, not merely read in
some documentation.

Every cycle logs its routing rationale — the cost stays explainable after the fact. Fallbacks
between models are native on the OpenRouter side (the `models[]` field), so there is no retry
logic to write.

## Operator control

The prompt tells the model what it is allowed to do. That is not enough: a prompt is an
instruction, not an access control, and `escalate_to_human` is an action the model *chooses* to
call — so the autonomy limit would only exist for as long as the model felt like honouring it.

`domain/policy.py` enforces those limits in code. Every outbound action is evaluated **before**
execution, inside `ActionRegistry._guard`:

| Rule | Verdict | What it prevents |
|---|---|---|
| `mode_dry_run` | blocked | Any send at all, in rehearsal or under test |
| `destinataire_hors_liste` | blocked | Writing to a real prospect during a demo |
| `prix_hors_catalogue` | blocked | A hallucinated price committing the company |
| `cadence_maximale` | blocked | Harassing a prospect |
| `mode_supervise` | approval | Any action, as long as autonomy has not been granted |
| `relecture_*` | approval | Anything a sales director would not let out the door |
| `montant_eleve` | approval | A large deal being played out without supervision |

Three properties here are deliberate:

**The default mode is `supervised`.** The system ships locked down and autonomy is granted
explicitly. The opposite would make the prospect bear the cost of a mistake.

**A refusal is returned to the model as a message, not an exception.** The agent can then adapt
— rewrite, switch channel, escalate. The prompt does, however, explicitly forbid it from
rephrasing to slip past the filter: dropping the word "discount" while offering the same
concession would be a serious offence.

**A human approval lifts the approval rules, never the blocks.** An operator arbitrates the
sales call; they do not bypass simulated mode or the allow-list of recipients, which are
operational guardrails.

Arbitration happens without a UI, via CLI or API: `approvals list` shows the exact message that
will go out, `approve` sends it as-is, `reject` files it away while leaving a trace in the CRM.
As long as an action sits in that queue, **nothing has reached the prospect**.

### Review, in two layers

Keyword detection catches what names itself ("discount", "-20%"). It does not see what merely
phrases itself: "I'll match their price", "you'll break even in six months, guaranteed", "the
offer expires tonight". Those sentences commit the company or damage the relationship without
containing a single dictionary word.

Hence a second layer: a model reads the message the way a sales director would before it goes
out, with the opportunity's context — stage, amount, open objections, number of exchanges. The
same sentence is not judged the same way on first contact and at the end of a negotiation. It
classifies into eight grounds: price commitment, contractual commitment, unsupportable promise,
premature concession, excessive pressure, customer reference, message inappropriate for the
stage, legal exposure — and quotes the offending sentence, the way a manager points at the
line.

Four deliberate positions:

**The layers stack, they do not replace one another.** The lexical baseline stays, always.
Trading a deterministic guarantee for a probabilistic judgement would be a regression: a
classifier gets things wrong, goes down, and can be worked around too.

**The baseline runs first, and short-circuits.** If it has already ruled, there is no point
paying for a model call to confirm a certainty — and the rationale remains auditable.

**Fail closed.** Model unreachable, unreadable JSON, invented category: the message is held. An
absent reviewer does not mean an approved message.

**Over-blocking is a risk that is handled, not ignored.** A reviewer that holds everything is
not being careful: an operator facing a queue full of false positives ends up approving
everything without reading, and the control disappears. The review prompt therefore explicitly
lists what must pass without comment — an initial approach, a courteous follow-up, a restatement
of list price, a reply to an objection that argues on value.

Cost: one call to the cheap model per outbound action, i.e. ~$0.0003. Review only runs in
autonomous mode — in the other modes the verdict is known in advance.

## The voice loop, closed

```
place_phone_call ──> POST /v2/create-phone-call (context serialised as strings)
       │
   during the call ──> Retell calls POST /retell/tool-call ──> ActionRegistry
       │
   end of call ──> call_analyzed ──> transcript + summary ──> decision cycle ──> CRM
```

Three verified constraints of the Retell API are wired into the code:
`retell_llm_dynamic_variables` only accepts **strings** (hence the explicit flattening of the
context); it is `call_analyzed`, not `call_ended`, that carries the summary and the sentiment;
and the custom-function payload really is `{name, args, call}`.

GPT-5.6 is not among the models Retell offers natively: the voice agent runs on their model,
our engine keeps the strategy before and after the call, and serves its actions during it. The
action registry is shared between the two — the text agent and the voice agent execute the same
code, not two copies doomed to diverge.

## The CRM as the single source of truth

What the agent learns (objections, decisions, interactions) is written into HubSpot as prefixed
notes — readable by a human in the UI, re-parsable by the agent on the next scan. No hidden
database sitting next to the CRM that sales reps would never see.

An accepted corollary: an action that leaves no trace in the CRM did not happen, from the human
sales rep's point of view. Every action in the registry writes its trace.

Only the scanner's execution state (pointer, scheduled follow-ups, cadence log) lives alongside:
that is the agent's memory, not the sales truth, and the two do not share the same lifecycle.

## Sponsors integrated

Three, chosen because they carry a real function of the product — not to tick boxes.

| Sponsor | Function carried | Where the code lives |
|---|---|---|
| **OpenAI + OpenRouter** | The reasoning, its stakes-based routing, message review | Entirely on our side (`adapters/intelligence/`) |
| **Trigger.dev** | Autonomy: the scan cron, durable follow-ups | Code on our side, **execution on their cloud** |
| **Exa** | The "what don't I know?": news about the prospect | Entirely on our side (`adapters/intelligence/exa_enrichment.py`) |

Deliberately set aside: **Auth0** (most of it is dashboard configuration, and its contribution —
human approval — was only demonstrable with a UI), **Ambiguous AI** (no public API documentation
found, impossible to plan around), **Mozilla** (`any-llm` solves the same problem as OpenRouter;
doing both would be incoherent).

## Costs

Rates taken from the OpenRouter catalogue, not from third-party documentation:

| Model | Input / output per MTok | Cost of one cycle |
|---|---|---|
| `openai/gpt-5.6-luna` | $0.20 / $1.20 | ~$0.001–0.003 |
| `openai/gpt-5.6-terra` | $2.00 / $12.00 | — (fallback) |
| `openai/gpt-5.6-sol` | $2.00 / $10.00 | ~$0.008–0.02 |

| Other line item | Cost |
|---|---|
| Review of one outbound message | ~$0.0003 |
| Exa search | ~$0.007 per request ($20 of free credits, no card) |
| Retell phone call, 5 minutes | ~$0.65 |

Two observations. First, Sol is **significantly cheaper than advertised** in public comparisons
($2/$10 rather than $5/$30), to the point of costing less than Terra on output — so routing to
the capable model carries little penalty. Second, the LLM is never the dominant line item:
**voice is, by a factor of 30 to 600**. That is what justifies the agent reserving the phone for
the moments where it adds more than writing does.

## What is left to do

- **Test the reviewer's judgement** on real messages as soon as an OpenRouter key is available.
  The behaviour *around* the model is tested; its judgement is not.
- **Verify the exact scheme of the Retell signature** (`X-Retell-Signature`) against their SDK:
  the current implementation does a standard HMAC-SHA256.
- **Load the Kaggle dataset** "CRM Sales Opportunities" into HubSpot for a realistic book of
  business (`accounts.csv`, `products.csv`).
- **Migrate to HubSpot's date-based versioning** (`/crm/objects/2026-09/`): the v3/v4 paths in
  use remain supported, but are no longer the recommendation.
- **Replace the JSON adapters with Postgres** beyond a single instance — the port does not
  change.
