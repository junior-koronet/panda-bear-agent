"""
Panda Bear — Incident Classifier
Detects, classifies, explains, and records every operational error.
No error is generic. Every incident has a type, a cause, and a solution.
"""

from datetime import datetime
from db.schema import get_db


class IncidentType:
    MISSING_SCOPE    = "missing_scope"
    CHANNEL_NOT_FOUND = "channel_not_found"
    TIMEOUT          = "timeout"
    DATABASE_LOCKED  = "database_locked"
    SERVICE_UNAVAILABLE = "service_unavailable"
    AUTH_FAILURE     = "auth_failure"
    RATE_LIMITED     = "rate_limited"
    USER_NOT_FOUND   = "user_not_found"
    UNKNOWN          = "unknown"


_SOLUTIONS = {
    IncidentType.MISSING_SCOPE: {
        "cause": "El bot de Slack no tiene el permiso OAuth necesario para esta operación.",
        "steps": [
            "1. Ve a https://api.slack.com/apps",
            "2. Selecciona tu app → OAuth & Permissions → Bot Token Scopes",
            "3. Agrega el scope indicado en el incidente",
            "4. Scroll down → 'Reinstall to Workspace' → confirma la instalación",
            "5. Copia el nuevo Bot Token al .env (SLACK_BOT_TOKEN)",
            "6. Reinicia el servidor: python main.py",
        ],
        "recoverable": False,
    },
    IncidentType.CHANNEL_NOT_FOUND: {
        "cause": "El canal de Slack no existe o el bot no tiene acceso a él.",
        "steps": [
            "1. Verifica que el canal exista en Slack",
            "2. Invita al bot: /invite @onboarding_bot_korone en el canal",
            "3. O actualiza BIENVENIDAS_CHANNEL en .env con el nombre correcto",
            "4. Reinicia el servidor",
        ],
        "recoverable": False,
    },
    IncidentType.TIMEOUT: {
        "cause": "La llamada a la API externa tardó demasiado tiempo en responder.",
        "steps": [
            "1. Verifica la conexión a internet del servidor",
            "2. El servicio externo puede estar temporalmente lento",
            "3. Panda Bear reintentará automáticamente en el próximo ciclo",
        ],
        "recoverable": True,
    },
    IncidentType.DATABASE_LOCKED: {
        "cause": "SQLite recibió múltiples escrituras concurrentes y una connexión bloqueó a otra.",
        "steps": [
            "1. El servidor se auto-recupera en la mayoría de casos (busy_timeout=30s)",
            "2. Si persiste, reinicia el servidor: python main.py",
            "3. Verifica que no haya otro proceso usando la misma base de datos",
        ],
        "recoverable": True,
    },
    IncidentType.SERVICE_UNAVAILABLE: {
        "cause": "El servicio externo no está disponible o las credenciales son incorrectas.",
        "steps": [
            "1. Verifica el estado del servicio en su página de status",
            "2. Revisa que las credenciales en .env sean correctas y no hayan expirado",
            "3. Panda Bear intentará reconectarse automáticamente",
        ],
        "recoverable": True,
    },
    IncidentType.AUTH_FAILURE: {
        "cause": "Las credenciales son inválidas, han expirado, o fueron revocadas.",
        "steps": [
            "1. Verifica las credenciales en .env (API keys y tokens)",
            "2. Regenera el token desde la plataforma correspondiente",
            "3. Actualiza .env y reinicia el servidor",
        ],
        "recoverable": False,
    },
    IncidentType.RATE_LIMITED: {
        "cause": "Se realizaron demasiadas peticiones al servicio en un período corto.",
        "steps": [
            "1. Panda Bear esperará automáticamente antes de reintentar",
            "2. Si persiste, reduce la frecuencia de sync (lookbackDays más alto)",
        ],
        "recoverable": True,
    },
    IncidentType.USER_NOT_FOUND: {
        "cause": "El usuario no existe en Slack o no es miembro del workspace.",
        "steps": [
            "1. Verifica que el empleado o manager tenga cuenta activa en Slack",
            "2. Confirma que el email o nombre de usuario sea correcto en BambooHR",
            "3. El mensaje fue marcado como fallido — puedes reenviarlo manualmente",
        ],
        "recoverable": False,
    },
}


def classify_error(error: str, service: str = "") -> dict:
    """
    Classifies an error string into an actionable incident.
    Returns type, cause, solution steps, and recoverability.
    """
    err = error.lower()

    if "missing_scope" in err or ("scope" in err and "needed" in err):
        needed = ""
        try:
            if "'needed': '" in error:
                needed = error.split("'needed': '")[1].split("'")[0]
        except Exception:
            pass
        sol = _SOLUTIONS[IncidentType.MISSING_SCOPE]
        steps = list(sol["steps"])
        if needed:
            steps.insert(2, f"   Scope requerido: '{needed}'")
        return {
            "type": IncidentType.MISSING_SCOPE,
            "cause": sol["cause"],
            "solution": steps,
            "recoverable": False,
            "detail": {"missingScope": needed},
        }

    if "channel_not_found" in err or "no_channel" in err:
        sol = _SOLUTIONS[IncidentType.CHANNEL_NOT_FOUND]
        return {"type": IncidentType.CHANNEL_NOT_FOUND, "cause": sol["cause"],
                "solution": sol["steps"], "recoverable": False, "detail": {}}

    if "not_in_channel" in err or "user not found" in err or "user_not_found" in err:
        sol = _SOLUTIONS[IncidentType.USER_NOT_FOUND]
        return {"type": IncidentType.USER_NOT_FOUND, "cause": sol["cause"],
                "solution": sol["steps"], "recoverable": False, "detail": {}}

    if "database is locked" in err or ("locked" in err and "sqlite" not in err and "database" in err):
        sol = _SOLUTIONS[IncidentType.DATABASE_LOCKED]
        return {"type": IncidentType.DATABASE_LOCKED, "cause": sol["cause"],
                "solution": sol["steps"], "recoverable": True, "detail": {}}

    if "timeout" in err or "timed out" in err or "read timeout" in err:
        sol = _SOLUTIONS[IncidentType.TIMEOUT]
        return {"type": IncidentType.TIMEOUT, "cause": sol["cause"],
                "solution": sol["steps"], "recoverable": True, "detail": {}}

    if "ratelimited" in err or "rate_limited" in err or "429" in err:
        sol = _SOLUTIONS[IncidentType.RATE_LIMITED]
        return {"type": IncidentType.RATE_LIMITED, "cause": sol["cause"],
                "solution": sol["steps"], "recoverable": True, "detail": {}}

    if any(x in err for x in ["invalid_auth", "not_authed", "token_revoked", "401", "403"]):
        sol = _SOLUTIONS[IncidentType.AUTH_FAILURE]
        return {"type": IncidentType.AUTH_FAILURE, "cause": sol["cause"],
                "solution": sol["steps"], "recoverable": False, "detail": {}}

    if any(x in err for x in ["unavailable", "connection", "503", "502", "500", "econnrefused"]):
        sol = _SOLUTIONS[IncidentType.SERVICE_UNAVAILABLE]
        return {"type": IncidentType.SERVICE_UNAVAILABLE, "cause": sol["cause"],
                "solution": sol["steps"], "recoverable": True, "detail": {}}

    return {
        "type": IncidentType.UNKNOWN,
        "cause": "Error desconocido. Revisa los logs del servidor para más detalles.",
        "solution": ["Revisa la consola del servidor para el traceback completo.",
                     "Si persiste, abre un issue con el mensaje de error exacto."],
        "recoverable": False,
        "detail": {},
    }


def record_incident(service: str, error: str, classification: dict,
                    recovered: bool = False, recovery_attempts: int = 0) -> int:
    """Records an incident in the database. Returns the incident ID."""
    import json
    conn = get_db()
    try:
        cursor = conn.execute("""
            INSERT INTO incidents
            (service, type, error, cause, solution, recoverable, recovered,
             recoveryAttempts, resolved, createdAt)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
        """, (
            service,
            classification.get("type", IncidentType.UNKNOWN),
            error[:2000],
            classification.get("cause", ""),
            "\n".join(classification.get("solution", [])),
            1 if classification.get("recoverable") else 0,
            1 if recovered else 0,
            recovery_attempts,
            datetime.now().isoformat(),
        ))
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def resolve_incident(incident_id: int) -> None:
    conn = get_db()
    try:
        conn.execute(
            "UPDATE incidents SET resolved=1, resolvedAt=? WHERE id=?",
            (datetime.now().isoformat(), incident_id),
        )
        conn.commit()
    finally:
        conn.close()


def get_active_incidents(limit: int = 20) -> list:
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM incidents WHERE resolved=0 ORDER BY createdAt DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_incident_history(limit: int = 100) -> list:
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM incidents ORDER BY createdAt DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
