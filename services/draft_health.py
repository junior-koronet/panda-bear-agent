"""
Draft preflight — checks, BEFORE Junior approves anything, that every piece
needed to create the onboarding drafts works:

  gmail        Junior's Gmail token can read drafts (email draft)
  slack_bot    the Panda Bear bot token works (it resolves the leader / channel)
  claude_slack headless Claude Code is logged in and Junior's Slack connector
               exposes slack_send_message_draft (Slack drafts)

Nothing is created, sent or drafted here: Claude may only use ToolSearch and
is told not to call any Slack tool. Runs at startup and before each scheduled
sync; Junior gets a DM when something breaks (and when it's fixed), so a
broken dependency is found before an approval fails, not after.
"""

import json
import os
import subprocess
import threading
from datetime import datetime

from services.slack_draft_worker import DISALLOWED_TOOLS, claude_bin

LABELS = {"gmail": "Gmail (correo)", "slack_bot": "Bot de Slack", "claude_slack": "Claude + conector de Slack"}
FIXES = {
    "gmail": "corre 'python setup_google_auth.py' en C:\\PandaBearAgent",
    "slack_bot": "revisa SLACK_BOT_TOKEN en .env",
    "claude_slack": "abre Claude Code en este PC y revisa que tengas sesión iniciada y el conector "
                    "\"claude.ai Slack\" conectado",
}
CLAUDE_PROMPT = (
    "Chequeo automático de Panda Bear. NO crees, envíes ni programes nada y NO llames ninguna "
    "herramienta de Slack. Solo usa ToolSearch con \"select:mcp__claude_ai_Slack__slack_send_message_draft\" "
    "y responde ÚNICAMENTE una línea JSON: {\"ok\": true} si ToolSearch devolvió la definición de esa "
    "herramienta, o {\"ok\": false, \"error\": \"<motivo>\"} si no."
)

_lock = threading.Lock()
_last: dict | None = None


def _check_gmail(google) -> dict:
    if not google or not google.gmail:
        return {"ok": False, "error": "Gmail no está conectado"}
    google.gmail.users().drafts().list(userId="me", maxResults=1).execute()
    return {"ok": True}


def _check_slack_bot(slack) -> dict:
    if not slack or not slack.client:
        return {"ok": False, "error": "el bot de Slack no está conectado"}
    slack.client.auth_test()
    return {"ok": True}


def _check_claude_slack() -> dict:
    exe = claude_bin()
    if not exe:
        return {"ok": False, "error": "no encontré Claude Code (CLAUDE_BIN)"}
    proc = subprocess.run(
        [exe, "-p", "--output-format", "json", "--model", os.getenv("DRAFT_CHECK_MODEL", "haiku"),
         "--allowedTools", "ToolSearch", "--disallowedTools", DISALLOWED_TOOLS],
        input=CLAUDE_PROMPT, capture_output=True, text=True, encoding="utf-8", timeout=180,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )
    try:
        envelope = json.loads(proc.stdout)
    except (json.JSONDecodeError, TypeError):
        return {"ok": False, "error": f"Claude no respondió bien (exit {proc.returncode}): {(proc.stderr or proc.stdout or '')[:200]}"}
    if envelope.get("is_error"):
        return {"ok": False, "error": f"Claude falló: {str(envelope.get('result'))[:200]}"}
    text = (envelope.get("result") or "").strip()
    try:
        result = json.loads(text[text.find("{"):text.rfind("}") + 1])
    except (json.JSONDecodeError, ValueError):
        return {"ok": False, "error": f"respuesta sin JSON: {text[:200]}"}
    return {"ok": bool(result.get("ok")), "error": result.get("error")}


def run_checks(google=None, slack=None, notify=None) -> dict:
    """Runs every check, stores the result and DMs Junior when the overall state changes."""
    global _last
    with _lock:
        checks = {}
        for key, fn in (("gmail", lambda: _check_gmail(google)), ("slack_bot", lambda: _check_slack_bot(slack)),
                        ("claude_slack", _check_claude_slack)):
            try:
                checks[key] = fn()
            except Exception as e:
                checks[key] = {"ok": False, "error": f"{type(e).__name__}: {str(e)[:200]}"}
        result = {"checkedAt": datetime.now().isoformat(), "ok": all(c["ok"] for c in checks.values()),
                  "checks": checks}
        previous, _last = _last, result
    broken = [k for k, c in checks.items() if not c["ok"]]
    print(f"[draft_health] {'OK' if result['ok'] else 'FALLA: ' + ', '.join(broken)}")

    if notify:
        if broken and (previous is None or previous["ok"] or
                       {k for k, c in previous["checks"].items() if not c["ok"]} != set(broken)):
            lines = [f"• *{LABELS[k]}*: {checks[k].get('error') or 'no responde'} → {FIXES[k]}" for k in broken]
            notify("🐼 ⚠️ *Ahora mismo NO podría crear algunos borradores de onboarding.*\n" + "\n".join(lines)
                   + "\nArréglalo antes de aprobar; lo vuelvo a revisar antes de cada sync.")
        elif not broken and previous is not None and not previous["ok"]:
            notify("🐼 ✅ Todo listo otra vez: Gmail, Slack y Claude responden. Los borradores se pueden crear.")
    return result


def last_result() -> dict | None:
    return _last
