"""
Auth — session-based dashboard login.
Replaces HTTP Basic Auth (the native browser popup) with a real login form
+ cookie session: browsers/password managers handle a normal form submit
far more reliably than a Basic Auth challenge, and some Google Workspace
admin policies block password-saving specifically for that native popup.

Scripts/curl can still authenticate with a raw `Authorization: Basic` header
(see verify_session_or_basic below) — but we never send back the
WWW-Authenticate challenge that makes browsers pop the native dialog.
"""

import base64
import os
import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from db.schema import get_db

DASHBOARD_USER = os.getenv("DASHBOARD_USER", "junior@koronet.com")
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "koronet2024")
SESSION_COOKIE_NAME = "panda_bear_session"
SESSION_DAYS = 30

router = APIRouter()  # intentionally NOT behind the auth dependency


def _create_session() -> str:
    token = secrets.token_urlsafe(32)
    now = datetime.now()
    expires = now + timedelta(days=SESSION_DAYS)
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO sessions (token, createdAt, expiresAt) VALUES (?, ?, ?)",
            (token, now.isoformat(), expires.isoformat()),
        )
        conn.commit()
    finally:
        conn.close()
    return token


def is_valid_session(token: str | None) -> bool:
    if not token:
        return False
    conn = get_db()
    try:
        row = conn.execute("SELECT expiresAt FROM sessions WHERE token=?", (token,)).fetchone()
    finally:
        conn.close()
    return bool(row) and datetime.fromisoformat(row["expiresAt"]) >= datetime.now()


def verify_session_or_basic(request: Request) -> str:
    """
    Router-level auth dependency. Cookie session is the primary path (what
    the login form sets). Raw Basic Auth header is accepted as a fallback
    for scripts/curl, but we deliberately never raise with a
    WWW-Authenticate header — that's what triggers the browser's native
    popup we're trying to get away from. A plain 401 lets the dashboard's
    own JS redirect to /login instead.
    """
    if is_valid_session(request.cookies.get(SESSION_COOKIE_NAME)):
        return DASHBOARD_USER

    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("basic "):
        try:
            decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
            username, _, password = decoded.partition(":")
        except Exception:
            username, password = "", ""
        if secrets.compare_digest(username, DASHBOARD_USER) and secrets.compare_digest(password, DASHBOARD_PASSWORD):
            return DASHBOARD_USER

    raise HTTPException(status_code=401, detail="No autenticado")


@router.post("/api/auth/login")
def login(username: str = Form(...), password: str = Form(...)):
    if not (secrets.compare_digest(username, DASHBOARD_USER) and secrets.compare_digest(password, DASHBOARD_PASSWORD)):
        return RedirectResponse(url="/login?error=1", status_code=303)

    token = _create_session()
    resp = RedirectResponse(url="/", status_code=303)
    resp.set_cookie(
        key=SESSION_COOKIE_NAME, value=token, httponly=True, samesite="lax",
        max_age=SESSION_DAYS * 24 * 3600, path="/",
    )
    return resp


@router.post("/api/auth/logout")
def logout(request: Request):
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token:
        conn = get_db()
        try:
            conn.execute("DELETE FROM sessions WHERE token=?", (token,))
            conn.commit()
        finally:
            conn.close()
    resp = RedirectResponse(url="/login", status_code=303)
    resp.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return resp
