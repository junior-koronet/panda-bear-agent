"""
One-time script — logs into Junior's own Google account inside a dedicated,
persistent Chromium profile that the meeting-join automation reuses. Run
this once, and again any time the session expires (Google asks to sign in
again, 2FA challenge, etc).

Keeps the browser open for a few minutes so there's time to complete login
(including any two-step verification), then saves and closes automatically.

Usage:
    python setup_meeting_bot_auth.py
"""

import os
import time
from dotenv import load_dotenv

load_dotenv()

from playwright.sync_api import sync_playwright

PROFILE_DIR = os.getenv("MEETING_BOT_PROFILE_DIR", "meeting_bot_profile")
WAIT_MINUTES = 4


def main():
    os.makedirs(PROFILE_DIR, exist_ok=True)
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(PROFILE_DIR, headless=False)
        page = context.new_page()
        page.goto("https://accounts.google.com/")

        print(f"Inicia sesion con tu cuenta de Google en la ventana que se abrio.")
        print(f"Tienes {WAIT_MINUTES} minutos. La sesion se guarda automaticamente al cerrar.")

        for remaining in range(WAIT_MINUTES, 0, -1):
            print(f"  ... {remaining} minuto(s) restante(s)")
            time.sleep(60)

        context.close()

    print(f"Listo -- la sesion quedo guardada en '{PROFILE_DIR}'.")


if __name__ == "__main__":
    main()
