"""
One-time script — authorizes Panda Bear to access Junior's Google Calendar,
Gmail, Docs and Slides. Opens a browser for consent and saves the resulting
token. Re-run this any time services/google_service.py's SCOPES list grows —
a stale token only carries the scopes it was originally granted with.

Usage:
    python setup_google_auth.py
"""

import os
from dotenv import load_dotenv

load_dotenv()

from google_auth_oauthlib.flow import InstalledAppFlow
from services.google_service import SCOPES

CREDENTIALS_PATH = os.getenv("GOOGLE_CREDENTIALS_PATH", "google_credentials.json")
TOKEN_PATH = os.getenv("GOOGLE_TOKEN_PATH", "google_token.json")


def main():
    if not os.path.exists(CREDENTIALS_PATH):
        print(f"No encontre {CREDENTIALS_PATH}. Descarga las credenciales OAuth desde Google Cloud Console primero.")
        return

    flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_PATH, SCOPES)
    creds = flow.run_local_server(port=0)

    with open(TOKEN_PATH, "w") as f:
        f.write(creds.to_json())

    print(f"Listo -- token guardado en {TOKEN_PATH}. Scopes autorizados: {SCOPES}")


if __name__ == "__main__":
    main()
