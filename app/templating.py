import hashlib
from functools import lru_cache
from pathlib import Path

from fastapi.templating import Jinja2Templates

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

UNKNOWN_FLAG = "\N{GLOBE WITH MERIDIANS}"
_REGIONAL_INDICATOR_A = 0x1F1E6


def country_flag(country_code: str) -> str:
    """Turn an ISO 3166-1 alpha-2 code into its flag emoji.

    Anything else -- including the "Unknown" bucket -- gets a globe, so every
    row in the table lines up whether or not the country resolved.
    """
    code = (country_code or "").strip().upper()
    if len(code) != 2 or not code.isalpha():
        return UNKNOWN_FLAG

    return "".join(chr(_REGIONAL_INDICATOR_A + ord(letter) - ord("A")) for letter in code)


@lru_cache
def asset_url(filename: str) -> str:
    """A static URL carrying a hash of the file's contents.

    Without it, a browser holding yesterday's stylesheet keeps using it after a
    deploy, and the new markup renders against the old CSS. Deliberately not
    applied to beacon.js: customers paste that URL into their own pages, so it
    has to stay stable.
    """
    path = STATIC_DIR / filename
    if not path.is_file():
        return f"/static/{filename}"

    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:10]
    return f"/static/{filename}?v={digest}"


templates = Jinja2Templates(directory=TEMPLATES_DIR)
templates.env.globals["asset"] = asset_url
templates.env.filters["comma"] = lambda value: f"{value:,}"
templates.env.filters["flag"] = country_flag
