"""
Slack draft worker — turns approved Slack messages into REAL Slack drafts.

Panda Bear's bot token can only post (= send), never draft. Claude, through
Junior's own Slack connector, can place a message in a chat's composer as a
draft (slack_send_message_draft) — exactly what Junior does by hand. So right
after an approval (and every few minutes as a sweep) this runs headless Claude
Code for each 'ready_for_slack_draft' message.

Safety: Claude is launched with ONLY ToolSearch + slack_send_message_draft
allowed and every sending tool explicitly disallowed — it is technically
unable to send or schedule anything. Approving = creating drafts, never
sending (DECISIONS.md).

Outcome per message:
  draft_created → Slack accepted the draft; Junior gets the confirmation DM.
  failed        → after MAX_ATTEMPTS tries, with the exact reason in
                  deliveryError + a DM to Junior. Never silent.
"""

import json
import os
import shutil
import subprocess
import threading
from datetime import datetime

from db.schema import get_db

MAX_ATTEMPTS = 3
TIMEOUT_SECONDS = int(os.getenv("SLACK_DRAFT_TIMEOUT_SECONDS", 240))
ALLOWED_TOOLS = "ToolSearch,mcp__claude_ai_Slack__slack_send_message_draft"
DISALLOWED_TOOLS = ",".join([
    "mcp__claude_ai_Slack__slack_send_message",
    "mcp__claude_ai_Slack__slack_schedule_message",
    "Bash", "PowerShell", "Write", "Edit",
])
_lock = threading.Lock()

PROMPT = """Tarea automática de Panda Bear (sin usuario presente). Haz EXACTAMENTE esto y nada más:

1. Si la herramienta mcp__claude_ai_Slack__slack_send_message_draft no está cargada, cárgala con ToolSearch ("select:mcp__claude_ai_Slack__slack_send_message_draft").
2. Llámala UNA sola vez con:
   - channel_id: el valor "channel_id" del JSON de abajo
   - message: el valor "message" del JSON de abajo, copiado carácter por carácter, sin cambiar, traducir, resumir ni agregar nada.
3. NO envíes ni programes ningún mensaje. Solo el borrador.
4. Responde ÚNICAMENTE con una línea JSON, sin texto alrededor:
   {{"ok": true, "draft_id": "<draft_id devuelto>"}}  o  {{"ok": false, "error": "<error devuelto por Slack o motivo>"}}

JSON de entrada:
{payload}
"""


def claude_bin() -> str | None:
    return os.getenv("CLAUDE_BIN") or shutil.which("claude")


def run_claude(channel_id: str, message: str) -> dict:
    """Runs headless Claude Code once. Returns {"ok": bool, "draft_id"?, "error"?}."""
    exe = claude_bin()
    if not exe:
        return {"ok": False, "error": "No encontré el ejecutable de Claude Code (CLAUDE_BIN)."}
    prompt = PROMPT.format(payload=json.dumps({"channel_id": channel_id, "message": message}, ensure_ascii=False))
    try:
        proc = subprocess.run(
            [exe, "-p", "--output-format", "json", "--model", os.getenv("SLACK_DRAFT_MODEL", "sonnet"),
             "--allowedTools", ALLOWED_TOOLS, "--disallowedTools", DISALLOWED_TOOLS],
            input=prompt, capture_output=True, text=True, encoding="utf-8",
            timeout=TIMEOUT_SECONDS, cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"Claude no respondió en {TIMEOUT_SECONDS}s"}
    try:
        envelope = json.loads(proc.stdout)
    except (json.JSONDecodeError, TypeError):
        return {"ok": False, "error": f"Salida inesperada de Claude (exit {proc.returncode}): {(proc.stderr or proc.stdout or '')[:300]}"}
    if envelope.get("is_error"):
        return {"ok": False, "error": f"Claude falló: {str(envelope.get('result'))[:300]}"}
    text = (envelope.get("result") or "").strip()
    start, end = text.find("{"), text.rfind("}")
    try:
        result = json.loads(text[start:end + 1])
    except (json.JSONDecodeError, ValueError):
        return {"ok": False, "error": f"Respuesta de Claude sin JSON: {text[:300]}"}
    if result.get("ok") and not result.get("draft_id"):
        return {"ok": False, "error": "Claude dijo ok pero sin draft_id"}
    return result


def confirmation_text(msg: dict) -> str:
    """Exact wording Junior asked for, sent the moment the draft exists."""
    name = msg["employeeName"]
    if msg["messageType"] == "manager_slack":
        return (f"🐼 Junior, ya creé el mensaje al manager de *{name}* "
                f"({msg.get('managerName') or 'manager sin asignar'}) — ve y envíalo.")
    dias = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
    try:
        dia = " el " + dias[datetime.strptime(msg["employeeHireDate"], "%Y-%m-%d").weekday()]
    except (ValueError, TypeError):
        dia = ""
    return (f"🐼 Junior, ya tengo creada la bienvenida de *{name}*{dia} — "
            f"carga la imagen y programa ese mensaje de bienvenida.")


def create_pending_slack_drafts(notify=None, runner=run_claude) -> list[dict]:
    """
    Creates a real Slack draft for every approved Slack message that doesn't
    have one yet. notify(text) DMs Junior. Single-flight: if a run is already
    in progress this returns immediately (the running one picks everything up).
    """
    if not _lock.acquire(blocking=False):
        return []
    done, tried = [], [0]
    try:
        while True:
            conn = get_db()
            try:
                row = conn.execute(
                    "SELECT * FROM messages WHERE status='ready_for_slack_draft' "
                    "AND slackTarget IS NOT NULL AND slackTarget != '' "
                    "AND COALESCE(slackDraftAttempts, 0) < ? "
                    f"AND id NOT IN ({','.join('?' * len(tried))}) ORDER BY id LIMIT 1",
                    (MAX_ATTEMPTS, *tried),
                ).fetchone()
            finally:
                conn.close()
            if not row:
                return done
            msg = dict(row)
            tried.append(msg["id"])  # a failure is retried on the next sweep, not in a tight loop
            try:
                result = runner(msg["slackTarget"], msg["messageContent"])
            except Exception as e:
                result = {"ok": False, "error": f"{type(e).__name__}: {e}"}

            conn = get_db()
            try:
                if result.get("ok"):
                    conn.execute(
                        "UPDATE messages SET status='draft_created', draftId=?, deliveryError=NULL, "
                        "deliveredAt=? WHERE id=? AND status='ready_for_slack_draft'",
                        (result["draft_id"], datetime.now().isoformat(), msg["id"]),
                    )
                    conn.commit()
                    outcome = {"id": msg["id"], "status": "draft_created", "draftId": result["draft_id"]}
                    text = confirmation_text(msg)
                else:
                    attempts = (msg.get("slackDraftAttempts") or 0) + 1
                    error = result.get("error") or "error desconocido"
                    final = attempts >= MAX_ATTEMPTS
                    conn.execute(
                        "UPDATE messages SET slackDraftAttempts=?, deliveryError=?, "
                        "status=CASE WHEN ? THEN 'failed' ELSE status END, "
                        "retries=CASE WHEN ? THEN COALESCE(retries, 0) + 1 ELSE retries END "
                        "WHERE id=?",
                        (attempts, f"Borrador de Slack no creado: {error}", final, final, msg["id"]),
                    )
                    conn.commit()
                    outcome = {"id": msg["id"], "status": "failed" if final else "retrying", "error": error}
                    # One DM when it gives up (not one per hourly re-try of the same failure)
                    text = None
                    if final and not (msg.get("retries") or 0):
                        label = "DM al líder" if msg["messageType"] == "manager_slack" else "bienvenida en #koronet"
                        text = (f"❌ Junior, NO pude crear el borrador de Slack ({label}) de *{msg['employeeName']}*: "
                                f"{error}. Lo verás en rojo en el dashboard (🔁 para reintentar).")
            finally:
                conn.close()
            if text and notify:
                try:
                    notify(text)
                except Exception as e:
                    print(f"[slack_draft_worker] notify failed: {e}")
            print(f"[slack_draft_worker] message {msg['id']} → {outcome}")
            done.append(outcome)
    finally:
        _lock.release()


def kick(notify=None) -> None:
    """Runs the worker in the background (used right after an approval)."""
    threading.Thread(
        target=create_pending_slack_drafts, kwargs={"notify": notify},
        daemon=True, name="slack-draft-worker",
    ).start()
