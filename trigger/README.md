# Autonomous cadence — Trigger.dev

This is the piece that turns a CLI into an autonomous agent. Without it, someone has to launch
the scan by hand, and "autonomous sales agent" becomes a misnomer.

The folder is **deployable as-is**: configuration, locked dependencies and tasks are all
shipped. `npx trigger.dev init` is not needed — it would only regenerate what is already here.

## Why TypeScript alongside a Python project

Trigger.dev does not provide a Python SDK (their `@trigger.dev/python` package is a build
extension that runs scripts inside a container, not an SDK). So the pattern used here is the one
they recommend: **thin TypeScript tasks that call our HTTP API**. All the sales reasoning stays
in Python; only the cadence lives here.

## Getting started

```bash
cd trigger
npm install

# 1. Create a project on cloud.trigger.dev, grab its reference (proj_xxx)
export TRIGGER_PROJECT_REF=proj_xxxxxxxx

# 2. Point at the decision engine's API
export REVENUE_AGENT_API_URL=https://your-tunnel.ngrok.app
export SCAN_SHARED_SECRET=...          # must match the one on the Python API

npm run dev        # local run, tasks visible in their dashboard
npm run typecheck  # checks the tasks against the SDK's types
```

Deployment:

```bash
export TRIGGER_ACCESS_TOKEN=tr_pat_...   # or `npx trigger.dev login` interactively
npm run deploy
```

In the cloud, declare `REVENUE_AGENT_API_URL` and `SCAN_SHARED_SECRET` on the dashboard's
*Environment Variables* page: the tasks read them via `process.env`. Locally, `trigger.dev dev`
automatically loads a `.env` present in this folder.

⚠️ The ngrok URL changes on every restart. It is an environment variable, never a hard-coded
value.

## The two tasks

| Task | Trigger | Role |
|---|---|---|
| `scan-crm` | cron `*/5 * * * *` | The heartbeat. Calls `POST /scan`, logs every decision taken. |
| `schedule-follow-up` | on demand | Durable wait (`wait.for`) then re-engagement. The run is suspended and its resources released — it resumes exactly where it left off, even weeks later. |

The cron remains the primary mechanism: scheduled follow-ups are stored on the Python side and
detected by triage, which guarantees a single source of truth for "when to wake this opportunity
up". `schedule-follow-up` serves one-off deadlines and demos (schedule a follow-up two minutes
out and watch it fire on its own).

The 5-minute cadence is not arbitrary: the HubSpot Search API is capped at **4 requests/second
shared** across all objects. Scanning more often would not surface more leads, it would only
burn through the quota.

## Versions

`@trigger.dev/sdk`, `@trigger.dev/build` and the `trigger.dev` CLI are aligned on 4.5.16, with a
committed `package-lock.json`. The tasks compile without error against the SDK's types
(`npm run typecheck`).
