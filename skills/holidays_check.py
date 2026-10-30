"""
Public holidays for any country (python-holidays), keyed by the country names
Koronet uses (BambooHR "location", hire location in English or Spanish).

Used by Agendamiento (business days for each session, holiday alerts) and by
Approvals (warn when a hire's start date is a holiday in Colombia — where
People Ops works — or in the hire's own country).
"""

import re
import unicodedata
from datetime import date, datetime, timedelta
from functools import lru_cache

import holidays
from holidays.registry import COUNTRIES

BASE_COUNTRY = "Colombia"  # Junior's country: his holidays decide who runs People Ops sessions

# Spanish (and a few alternate English) names → ISO code. English names come from the registry.
_ALIASES = {
    "espana": "ES", "mexico": "MX", "peru": "PE", "brasil": "BR", "paises bajos": "NL", "holanda": "NL",
    "estados unidos": "US", "usa": "US", "eeuu": "US", "united states of america": "US",
    "reino unido": "GB", "uk": "GB", "inglaterra": "GB", "alemania": "DE", "francia": "FR",
    "italia": "IT", "belgica": "BE", "suiza": "CH", "polonia": "PL", "rumania": "RO", "rumania ": "RO",
    "ucrania": "UA", "rusia": "RU", "turquia": "TR", "japon": "JP", "canada": "CA", "panama": "PA",
    "republica dominicana": "DO", "noruega": "NO", "suecia": "SE", "dinamarca": "DK", "finlandia": "FI",
    "irlanda": "IE", "grecia": "GR", "austria": "AT", "hungria": "HU", "chequia": "CZ",
    "republica checa": "CZ", "sudafrica": "ZA", "nueva zelanda": "NZ", "filipinas": "PH", "marruecos": "MA",
    "egipto": "EG", "india": "IN", "china": "CN", "corea del sur": "KR", "singapur": "SG", "malta": "MT",
}


def _norm(s: str) -> str:
    s = "".join(c for c in unicodedata.normalize("NFD", (s or "").lower()) if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", s).strip()


@lru_cache(maxsize=1)
def _name_index() -> dict:
    idx = dict(_ALIASES)
    for _module, (cls, code, *_rest) in COUNTRIES.items():
        idx.setdefault(_norm(re.sub(r"(?<!^)(?=[A-Z])", " ", cls)), code)  # "UnitedStates" → "united states"
        idx.setdefault(code.lower(), code)
    return idx


def country_code(location: str) -> str | None:
    """'Colombia', 'Bogotá, Colombia', 'Remote - Argentina', 'Países Bajos' → ISO code (None if unknown)."""
    loc = _norm(location)
    if not loc:
        return None
    idx = _name_index()
    if loc in idx:
        return idx[loc]
    # Longest country name contained in the location string wins ("Bogota, Colombia").
    hits = [(len(n), c) for n, c in idx.items() if len(n) > 3 and re.search(rf"\b{re.escape(n)}\b", loc)]
    return max(hits)[1] if hits else None


@lru_cache(maxsize=256)
def _calendar(code: str, year: int):
    try:
        return holidays.country_holidays(code, years=year, language="es")
    except Exception:
        try:
            return holidays.country_holidays(code, years=year)
        except Exception:
            return {}


def _as_date(d) -> date:
    return d if isinstance(d, date) else datetime.strptime(str(d)[:10], "%Y-%m-%d").date()


def holiday_name(d, location: str) -> str | None:
    code = country_code(location)
    if not code:
        return None
    d = _as_date(d)
    name = _calendar(code, d.year).get(d)
    # "Día de Todos los Santos (observado)" → "Día de Todos los Santos, trasladado" (reads better inside parentheses)
    return re.sub(r"\s*\((observado|observed)\)", ", trasladado", name) if name else None


def holidays_on(d, locations) -> list:
    """[{'country': 'Colombia', 'name': 'Día de Todos los Santos'}] for each location (deduplicated) with a holiday on d."""
    out, seen = [], set()
    for loc in locations:
        code = country_code(loc)
        if not code or code in seen:
            continue
        seen.add(code)
        name = holiday_name(d, loc)
        if name:
            out.append({"country": (loc or "").strip().title() if len(loc) > 3 else code, "name": name})
    return out


def is_business_day(d, locations) -> bool:
    d = _as_date(d)
    return d.weekday() < 5 and not holidays_on(d, locations)


def next_business_day(d, locations, include_today: bool = True) -> date:
    d = _as_date(d)
    if not include_today:
        d += timedelta(days=1)
    for _ in range(60):
        if is_business_day(d, locations):
            return d
        d += timedelta(days=1)
    return d


def start_date_alert(hire_date: str, location: str) -> str | None:
    """Approvals warning: the hire starts on a weekend or on a holiday in Colombia and/or their own country."""
    try:
        d = _as_date(hire_date)
    except (ValueError, TypeError):
        return None
    hits = holidays_on(d, [BASE_COUNTRY, location or ""])
    if not hits and d.weekday() < 5:
        return None
    if d.weekday() >= 5:
        return "cae en fin de semana"
    if len(hits) == 1:
        return f"es feriado en {hits[0]['country']} ({hits[0]['name']})"
    return "es feriado en " + " y en ".join(f"{h['country']} ({h['name']})" for h in hits)
