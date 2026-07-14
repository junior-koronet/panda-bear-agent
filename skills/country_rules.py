"""
Skill: Country Rules
Determines language, timezone, and onboarding schedule based on employee location.
"""

from skills.base import Skill

SPANISH_COUNTRIES = {
    "colombia", "mexico", "méxico", "argentina", "chile", "peru", "perú",
    "ecuador", "uruguay", "paraguay", "bolivia", "venezuela", "costa rica",
    "panama", "panamá", "guatemala", "honduras", "nicaragua", "el salvador",
    "república dominicana", "cuba", "puerto rico",
}

SPAIN_KEYWORDS = {"spain", "españa", "espana"}


class CountryRulesSkill(Skill):
    name = "country_rules"
    description = "Determines language, timezone, and onboarding time based on employee country."
    category = "planning"

    def _run(self, location: str = "", country: str = "") -> dict:
        combined = f"{location} {country}".lower().strip()

        if any(k in combined for k in SPAIN_KEYWORDS):
            rules = {
                "language": "es",
                "onboardingTime": "7:00 AM Colombia (1:00 PM España)",
                "ccEmail": None,
                "region": "Spain",
            }
        elif any(k in combined for k in SPANISH_COUNTRIES):
            rules = {
                "language": "es",
                "onboardingTime": "8:00 AM Colombia",
                "ccEmail": None,
                "region": "Latin America",
            }
        else:
            rules = {
                "language": "en",
                "onboardingTime": "8:30 AM Colombia",
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


def get_language_and_time(location: str, country: str) -> dict:
    """Standalone helper for backward compatibility."""
    skill = CountryRulesSkill()
    result = skill.execute(location=location, country=country)
    return result["result"]
