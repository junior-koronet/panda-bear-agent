"""
Webhooks — external services that call Panda Bear.
These routes are NOT behind the dashboard's Basic Auth (external services
can't do that handshake) — each one must verify its own signature/secret
instead. Currently: Fathom (meeting notes).
"""

import hashlib
import hmac
import json
import os

from fastapi import APIRouter, Request, HTTPException

router = APIRouter(prefix="/api/webhooks")
_agent = None  # Injected at startup


def set_agent(agent):
    global _agent
    _agent = agent


FATHOM_WEBHOOK_SECRET = os.getenv("FATHOM_WEBHOOK_SECRET", "")


def _verify_fathom_signature(raw_body: bytes, signature_header: str) -> bool:
    """
    Verifies the Fathom webhook signature (HMAC-SHA256 of the raw body,
    using FATHOM_WEBHOOK_SECRET). Confirm the exact header name and scheme
    against developers.fathom.ai when configuring the real webhook — this
    is a standard HMAC pattern used as a placeholder until then.
    If no secret is configured yet, verification is skipped (dev/testing
    only) — a warning is logged so this doesn't go unnoticed in production.
    """
    if not FATHOM_WEBHOOK_SECRET:
        print("[fathom_webhook] WARNING: FATHOM_WEBHOOK_SECRET not set — skipping signature verification")
        return True
    if not signature_header:
        return False
    expected = hmac.new(FATHOM_WEBHOOK_SECRET.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header)


@router.post("/fathom")
async def fathom_webhook(request: Request):
    raw_body = await request.body()
    signature = request.headers.get("X-Fathom-Signature", "")

    if not _verify_fathom_signature(raw_body, signature):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    payload = json.loads(raw_body)

    title = payload.get("meeting_title") or payload.get("title") or "Reunión sin título"
    fathom_meeting_id = str(payload.get("meeting_id") or payload.get("id") or "")
    transcript = payload.get("transcript", "")
    summary = payload.get("summary", "")
    action_items = payload.get("action_items", []) or []

    if not _agent:
        raise HTTPException(status_code=503, detail="Agent not initialized")

    meeting_note_id = _agent.ingest_meeting_content(
        title=title, transcript=transcript, summary=summary,
        action_items=action_items, fathom_meeting_id=fathom_meeting_id,
    )

    return {"received": True, "meetingNoteId": meeting_note_id}
