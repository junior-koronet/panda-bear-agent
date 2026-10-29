"""
Skill: Country Rules
Determines language, timezone, and onboarding schedule based on employee location.
"""

from datetime import datetime
from zoneinfo import ZoneInfo
from skills.base import Skill

SPANISH_COUNTRIES = {
    "colombia", "mexico", "méxico", "argentina", "chile", "peru", "perú",
    "ecuador", "uruguay", "paraguay", "bolivia", "venezuela", "costa rica",
    "panama", "panamá", "guatemala", "honduras", "nicaragua", "el salvador",
    "república dominicana", "cuba", "puerto rico",
}

SPAIN_KEYWORDS = {"spain", "españa", "espana"}

BASE_TZ = "America/Bogota"

# IANA zone + display label (for "hora {label}") for every country in
# SPANISH_COUNTRIES — used to translate the fixed Colombia-time onboarding
# session into the candidate's own local time, so the welcome email never
# just says "8:00 AM Colombia" for someone who isn't in Colombia.
COUNTRY_TIMEZONES = {
    "colombia": ("America/Bogota", "Colombia"),
    "mexico": ("America/Mexico_City", "México"),
    "méxico": ("America/Mexico_City", "México"),
    "argentina": ("America/Argentina/Buenos_Aires", "Argentina"),
    "chile": ("America/Santiago", "Chile"),
    "peru": ("America/Lima", "Perú"),
    "perú": ("America/Lima", "Perú"),
    "ecuador": ("America/Guayaquil", "Ecuador"),
    "uruguay": ("America/Montevideo", "Uruguay"),
    "paraguay": ("America/Asuncion", "Paraguay"),
    "bolivia": ("America/La_Paz", "Bolivia"),
    "venezuela": ("America/Caracas", "Venezuela"),
    "costa rica": ("America/Costa_Rica", "Costa Rica"),
    "panama": ("America/Panama", "Panamá"),
    "panamá": ("America/Panama", "Panamá"),
    "guatemala": ("America/Guatemala", "Guatemala"),
    "honduras": ("America/Tegucigalpa", "Honduras"),
    "nicaragua": ("America/Managua", "Nicaragua"),
    "el salvador": ("America/El_Salvador", "El Salvador"),
    "república dominicana": ("America/Santo_Domingo", "República Dominicana"),
    "cuba": ("America/Havana", "Cuba"),
    "puerto rico": ("America/Puerto_Rico", "Puerto Rico"),
}
SPAIN_TZ = ("Europe/Madrid", "España")


def _dual_time(hour: int, minute: int, target_tz: str, target_label: str, hire_date: str = "") -> str:
    """
    Formats "{Colombia local time} (hora Colombia) / {target local time} (hora {label})".
    Uses zoneinfo (not a hardcoded UTC-offset table) so DST in either zone on
    the actual hire date is handled correctly instead of guessed.
    """
    try:
        ref = datetime.strptime(hire_date, "%Y-%m-%d") if hire_date else datetime.today()
    except ValueError:
        ref = datetime.today()

    base_dt = datetime(ref.year, ref.month, ref.day, hour, minute, tzinfo=ZoneInfo(BASE_TZ))
    target_dt = base_dt.astimezone(ZoneInfo(target_tz))

    def fmt(dt):
        return dt.strftime("%I:%M %p").lower()

    if target_tz == BASE_TZ:
        return f"{fmt(base_dt)} (hora Colombia)"
    return f"{fmt(base_dt)} (hora Colombia) / {fmt(target_dt)} (hora {target_label})"


class CountryRulesSkill(Skill):
    name = "country_rules"
    description = "Determines language, timezone, and onboarding time based on employee country."
    category = "planning"

    def _run(self, location: str = "", country: str = "", hire_date: str = "") -> dict:
        combined = f"{location} {country}".lower().strip()
        matched_country = next((k for k in SPANISH_COUNTRIES if k in combined), None)

        if any(k in combined for k in SPAIN_KEYWORDS):
            tz, label = SPAIN_TZ
            rules = {
                "language": "es",
                "onboardingTime": _dual_time(7, 0, tz, label, hire_date),
                "ccEmail": None,
                "region": "Spain",
            }
        elif matched_country:
            tz, label = COUNTRY_TIMEZONES[matched_country]
            rules = {
                "language": "es",
                "onboardingTime": _dual_time(8, 0, tz, label, hire_date),
                "ccEmail": None,
                "region": "Latin America",
            }
        else:
            rules = {
                "language": "en",
                "onboardingTime": _dual_time(8, 30, BASE_TZ, "Colombia", hire_date),
                "ccEmail": "moira.gago@koronet.com",
                "region": "International",
            }

        return {
            "result": rules,
            "decision": f"Language={rules['language']} for location='{combined or 'unknown'}'",
            "reasoning": (
                f"Employee location '{combined}' matched region '{rules['region']}'. "
                f"Applying language '{rules['language']}' and onboarding time '{rules['onboardingTime']}'."
            ),
            "confidence": 0.95 if combined else 0.5,
        }


def get_language_and_time(location: str, country: str, hire_date: str = "") -> dict:
    """Standalone helper for backward compatibility."""
    skill = CountryRulesSkill()
    result = skill.execute(location=location, country=country, hire_date=hire_date)
    return result["result"]
