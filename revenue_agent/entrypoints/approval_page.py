"""Arbitration page — taking approval out of the terminal.

An approval queue that can only be consulted from the command line does not get consulted: the
sales rep who gets a Teams ping on their phone is not going to open a shell. This page is
therefore the link that makes control genuinely exercisable — it opens from the notification's
link, shows **the exact message that will go out**, and offers two buttons.

Deliberately dependency-free: a single self-contained HTML file, no framework, no CDN. The
control surface for real outbound actions should be as small as possible.
"""

from __future__ import annotations

from html import escape

from revenue_agent.domain.approvals import PendingApproval

_STYLE = """
:root { color-scheme: light dark; --bg:#fbfbfa; --card:#fff; --ink:#1a1a18; --muted:#6b6b66;
        --line:#e4e4e0; --accent:#1a6b4a; --danger:#8b2f2f; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#161614; --card:#1f1f1d; --ink:#ecece8; --muted:#9a9a94; --line:#32322e; }
}
* { box-sizing: border-box; }
body { margin:0; padding:16px; background:var(--bg); color:var(--ink);
       font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; }
main { max-width: 680px; margin: 0 auto; }
h1 { font-size: 19px; margin: 8px 0 4px; }
.count { color: var(--muted); font-size: 14px; margin-bottom: 20px; }
article { background:var(--card); border:1px solid var(--line); border-radius:10px;
          padding:16px; margin-bottom:16px; }
article:target { border-color: var(--accent); border-width: 2px; }
.head { display:flex; flex-wrap:wrap; gap:8px; align-items:baseline;
        justify-content:space-between; margin-bottom:10px; }
.company { font-weight:600; }
.chan { font-size:12px; text-transform:uppercase; letter-spacing:.04em; color:var(--muted); }
.why { background:rgba(127,127,127,.08); border-left:3px solid var(--accent);
       padding:10px 12px; border-radius:4px; margin-bottom:12px; font-size:14px; }
dl { margin:0 0 12px; font-size:14px; }
dt { color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.04em;
     margin-top:8px; }
dd { margin:2px 0 0; }
pre { white-space:pre-wrap; word-wrap:break-word; background:rgba(127,127,127,.08);
      padding:12px; border-radius:6px; margin:2px 0 0; font:13px/1.5 ui-monospace,monospace; }
.actions { display:flex; gap:10px; margin-top:14px; }
button { flex:1; padding:12px; font-size:15px; font-weight:600; border-radius:8px;
         border:1px solid var(--line); cursor:pointer; background:var(--card); color:var(--ink); }
button.ok { background:var(--accent); border-color:var(--accent); color:#fff; }
button.no { color:var(--danger); }
button:disabled { opacity:.5; cursor:default; }
.done { padding:12px; border-radius:8px; background:rgba(127,127,127,.1); font-size:14px; }
.empty { text-align:center; color:var(--muted); padding:48px 16px; }
"""

_SCRIPT = """
async function arbitrate(id, verdict, button) {
  const card = document.getElementById(id);
  card.querySelectorAll('button').forEach(b => b.disabled = true);
  button.textContent = 'Working…';
  try {
    const response = await fetch(
      `/approvals/${id}/${verdict}?token=${encodeURIComponent(TOKEN)}`,
      { method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reviewer: 'arbitration page' }) }
    );
    const data = await response.json();
    card.innerHTML = '<div class="done">' + escapeHtml(data.result ?? 'Done.') + '</div>';
  } catch (error) {
    button.textContent = 'Failed — retry';
    card.querySelectorAll('button').forEach(b => b.disabled = false);
  }
}
function escapeHtml(text) {
  const d = document.createElement('div');
  d.textContent = text;
  return d.innerHTML;
}
"""


def render(approvals: list[PendingApproval], token: str, company_name: str) -> str:
    """The full page. `token` is re-injected into the arbitration calls."""
    if approvals:
        cards = "\n".join(_card(approval) for approval in approvals)
        count = (
            f"{len(approvals)} action pending"
            if len(approvals) == 1
            else f"{len(approvals)} actions pending"
        )
    else:
        cards = (
            '<p class="empty">No action pending.<br>'
            "The agent has nothing awaiting approval.</p>"
        )
        count = "Empty queue"

    return f"""<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Arbitration — {escape(company_name)}</title>
<style>{_STYLE}</style>
</head>
<body>
<main>
  <h1>Actions awaiting approval</h1>
  <p class="count">{escape(count)} · nothing has reached the prospect</p>
  {cards}
</main>
<script>const TOKEN = {_json_string(token)};{_SCRIPT}</script>
</body>
</html>"""


def _card(approval: PendingApproval) -> str:
    payload = approval.payload
    details = []
    if payload.get("subject"):
        details.append(f"<dt>Subject</dt><dd>{escape(payload['subject'])}</dd>")

    corps = payload.get("body") or payload.get("message") or payload.get("objective") or ""
    if corps:
        label = "Call objective" if payload.get("objective") else "Message"
        details.append(f"<dt>{label}</dt><dd><pre>{escape(corps)}</pre></dd>")

    return f"""
  <article id="{escape(approval.id)}">
    <div class="head">
      <span class="company">{escape(approval.company)}</span>
      <span class="chan">{escape(approval.kind.value)} → {escape(approval.recipient)}</span>
    </div>
    <div class="why">{escape(approval.reason)}</div>
    <dl>{"".join(details)}</dl>
    <div class="actions">
      <button class="ok" onclick="arbitrate('{escape(approval.id)}', 'approve', this)">
        Approve and send
      </button>
      <button class="no" onclick="arbitrate('{escape(approval.id)}', 'reject', this)">
        Reject
      </button>
    </div>
  </article>"""


def _json_string(value: str) -> str:
    import json

    return json.dumps(value)
