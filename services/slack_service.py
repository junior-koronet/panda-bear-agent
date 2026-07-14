"""
Service: Slack
Manages the Slack bot connection. Also sends approved messages.
Never sends anything without going through the approval workflow first.
"""

import os
import threading
from services.base import ServiceBase

SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
SLACK_APP_TOKEN = os.getenv("SLACK_APP_TOKEN")


class SlackService(ServiceBase):
    name = "slack_service"
    description = "Manages the Slack bot connection and handles approved message delivery."

    def __init__(self, on_message=None):
        super().__init__()
        self._workspace = None
        self._bot_name = None
        self._client = None
        self._socket_client = None
        self._socket_thread = None
        self._on_message = on_message
        self._messages_sent: int = 0

    def _on_start(self) -> None:
        from slack_sdk import WebClient
        self._client = WebClient(token=SLACK_BOT_TOKEN)
        auth = self._client.auth_test()
        self._workspace = auth["team"]
        self._bot_name = auth["user"]
        print(f"[slack_service] Connected — Bot: {self._bot_name} | Workspace: {self._workspace}")

        if SLACK_APP_TOKEN and self._on_message:
            self._start_socket_mode()

    def _start_socket_mode(self) -> None:
        from slack_sdk.socket_mode import SocketModeClient
        from slack_sdk.socket_mode.response import SocketModeResponse

        def handle(client, req):
            client.send_socket_mode_response(SocketModeResponse(envelope_id=req.envelope_id))
            if req.type == "events_api":
                event = req.payload.get("event", {})
                if event.get("type") in ("message", "app_mention"):
                    if event.get("bot_id") or event.get("subtype"):
                        return
                    user_id = event.get("user", "unknown")
                    text = event.get("text", "").strip()
                    channel = event.get("channel")
                    if "<@" in text:
                        text = text.split(">", 1)[-1].strip()
                    if text and self._on_message:
                        reply = self._on_message(user_id=user_id, text=text)
                        self._client.chat_postMessage(channel=channel, text=reply)

        self._socket_client = SocketModeClient(app_token=SLACK_APP_TOKEN, web_client=self._client)
        self._socket_client.socket_mode_request_listeners.append(handle)
        self._socket_thread = threading.Thread(target=self._socket_client.connect, daemon=True)
        self._socket_thread.start()

    def _on_stop(self) -> None:
        if self._socket_client:
            try:
                self._socket_client.disconnect()
            except Exception:
                pass

    def _check_alive(self) -> bool:
        if not self._client:
            return False
        try:
            self._client.auth_test()
            return True
        except Exception:
            return False

    def _health_details(self) -> dict:
        return {
            "workspace": self._workspace,
            "botName": self._bot_name,
            "messagesSent": self._messages_sent,
        }

    def _get_metrics(self) -> dict:
        return {"messagesSent": self._messages_sent}

    def audit_scopes(self) -> dict:
        """
        Audits current OAuth scopes against what Panda Bear needs.
        Returns a full actionable report: what's present, what's missing, and how to fix it.
        """
        REQUIRED = {
            "chat:write":        {"desc": "Enviar mensajes a canales donde el bot es miembro",     "feature": "Todas las notificaciones",          "critical": True},
            "chat:write.public": {"desc": "Postear en canales públicos sin ser miembro",           "feature": "Publicar en #bienvenidas",           "critical": True},
            "channels:read":     {"desc": "Listar y encontrar canales públicos por nombre",        "feature": "Buscar el canal #bienvenidas",        "critical": True},
            "users:read":        {"desc": "Buscar usuarios por nombre en el workspace",            "feature": "Encontrar managers para DMs",         "critical": True},
            "users:read.email":  {"desc": "Buscar usuarios por dirección de email",               "feature": "Encontrar empleados por email",       "critical": True},
            "im:write":          {"desc": "Abrir conversaciones directas (DMs)",                  "feature": "DMs al manager y aprobador",          "critical": True},
            "files:write":       {"desc": "Subir archivos e imágenes al workspace",               "feature": "Imagen de bienvenida del empleado",   "critical": False},
            "groups:read":       {"desc": "Listar canales privados",                              "feature": "Soporte para #bienvenidas privado",   "critical": False},
        }

        provided = set()
        try:
            resp = self._client.auth_test()
            if hasattr(resp, "headers") and resp.headers:
                header = resp.headers.get("x-oauth-scopes", "")
                if header:
                    provided = {s.strip() for s in header.split(",") if s.strip()}
        except Exception:
            pass

        # Probe channels:read directly if header not available
        if not provided:
            for scope_test, method in [
                ("channels:read", lambda: self._client.conversations_list(limit=1)),
                ("users:read",    lambda: self._client.users_list(limit=1)),
                ("files:write",   lambda: None),
            ]:
                try:
                    method()
                    provided.add(scope_test)
                except Exception as e:
                    if "missing_scope" not in str(e):
                        provided.add(scope_test)

        missing = {k: v for k, v in REQUIRED.items() if k not in provided}
        present = {k: v for k, v in REQUIRED.items() if k in provided}

        fix_steps = []
        if missing:
            fix_steps = [
                "1. Ve a https://api.slack.com/apps",
                "2. Selecciona tu app → OAuth & Permissions → Bot Token Scopes",
                f"3. Agrega: {', '.join(missing.keys())}",
                "4. 'Reinstall to Workspace' → confirma",
                "5. Copia el nuevo SLACK_BOT_TOKEN al .env",
                "6. Reinicia: python main.py",
            ]

        return {
            "provided": sorted(list(provided)),
            "required": list(REQUIRED.keys()),
            "present": {k: v["desc"] for k, v in present.items()},
            "missing": {k: {"desc": v["desc"], "feature": v["feature"], "critical": v["critical"]}
                        for k, v in missing.items()},
            "complete": len(missing) == 0,
            "criticalMissing": [k for k, v in missing.items() if v["critical"]],
            "fixSteps": fix_steps,
        }

    def send_dm(self, user_email: str, text: str, file_path: str = None) -> dict:
        """Sends a DM to a user by email. ONLY called after approval."""
        if not self._client:
            return {"sent": False, "error": "Slack client not initialized"}
        try:
            users = self._client.users_list()
            target_id = None
            for user in users["members"]:
                if user.get("profile", {}).get("email", "").lower() == user_email.lower():
                    target_id = user["id"]
                    break
            if not target_id:
                return {"sent": False, "error": f"User {user_email} not found in Slack"}

            dm = self._client.conversations_open(users=[target_id])
            channel = dm["channel"]["id"]

            if file_path and os.path.exists(file_path):
                self._client.files_upload_v2(channel=channel, file=file_path, initial_comment=text)
            else:
                self._client.chat_postMessage(channel=channel, text=text)

            self._messages_sent += 1
            return {"sent": True, "userId": target_id}
        except Exception as e:
            return {"sent": False, "error": str(e)}

    def find_user_by_name(self, name: str) -> str | None:
        """Finds a Slack user ID by name (partial match)."""
        if not self._client:
            return None
        try:
            name_lower = name.lower()
            users = self._client.users_list()
            for user in users["members"]:
                if (name_lower in user.get("name", "").lower() or
                        name_lower in user.get("real_name", "").lower()):
                    return user["id"]
            return None
        except Exception:
            return None

    def post_to_channel(self, channel_name: str, text: str, file_path: str = None) -> dict:
        """Posts a message to a public Slack channel by name."""
        if not self._client:
            return {"sent": False, "error": "Slack client not initialized"}
        try:
            channels = self._client.conversations_list(types="public_channel,private_channel", limit=200)
            channel_id = None
            for ch in channels["channels"]:
                if ch["name"].lower() == channel_name.lower().lstrip("#"):
                    channel_id = ch["id"]
                    break
            if not channel_id:
                return {"sent": False, "error": f"Channel #{channel_name} not found"}

            if file_path and os.path.exists(file_path):
                self._client.files_upload_v2(channel=channel_id, file=file_path, initial_comment=text)
            else:
                self._client.chat_postMessage(channel=channel_id, text=text)

            self._messages_sent += 1
            return {"sent": True, "channel": channel_name}
        except Exception as e:
            return {"sent": False, "error": str(e)}

    def send_notification(self, user_id: str, text: str) -> dict:
        """Sends a notification DM to a specific user ID."""
        if not self._client:
            return {"sent": False, "error": "Slack client not initialized"}
        try:
            dm = self._client.conversations_open(users=[user_id])
            channel = dm["channel"]["id"]
            self._client.chat_postMessage(channel=channel, text=text)
            self._messages_sent += 1
            return {"sent": True}
        except Exception as e:
            return {"sent": False, "error": str(e)}

    @property
    def client(self):
        return self._client
