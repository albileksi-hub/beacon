import datetime as dt
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


def tick_label(bucket: str, interval: str) -> str:
    """Shorten a bucket label for an axis tick.

    The full ISO bucket is unambiguous and far too wide to repeat across an
    axis -- seven of them collide at any width this chart is drawn at. The
    hover title on each point still carries the unshortened label.
    """
    moment = dt.datetime.fromisoformat(bucket)
    if interval == "hour":
        return moment.strftime("%H:%M")
    if interval == "month":
        return moment.strftime("%b")

    # Written out rather than "%-d", which is a GNU extension: Windows wants
    # "%#d" and this project is developed on one.
    return f"{moment.day} {moment:%b}"


@lru_cache
def _digest(filename: str, fingerprint: tuple[int, int]) -> str:
    """The content hash, recomputed only when the file has actually changed.

    ``fingerprint`` is never read. It is in the signature so that it is part of
    the cache key: reading and hashing the file costs about ten microseconds
    and a stat costs one, so the stat decides whether the hash is still valid.

    The pair is (mtime_ns, size). An edit that preserved both would not be
    noticed, which is not something a person or a build step does.
    """
    return hashlib.sha256((STATIC_DIR / filename).read_bytes()).hexdigest()[:10]


def asset_url(filename: str) -> str:
    """A static URL carrying a hash of the file's contents.

    Without it, a browser holding yesterday's stylesheet keeps using it after a
    deploy, and the new markup renders against the old CSS. Deliberately not
    applied to beacon.js: customers paste that URL into their own pages, so it
    has to stay stable.

    The whole result used to be cached against the filename alone, which was
    right in production and wrong everywhere else: the process answered with
    the hash the file had at startup, so editing a stylesheet changed nothing
    until a restart and the browser went on serving the version it already had.
    Five assets a page at a microsecond each is not a reason to be wrong about
    that.
    """
    path = STATIC_DIR / filename
    if not path.is_file():
        return f"/static/{filename}"

    stat = path.stat()
    return f"/static/{filename}?v={_digest(filename, (stat.st_mtime_ns, stat.st_size))}"


templates = Jinja2Templates(directory=TEMPLATES_DIR)
templates.env.globals["asset"] = asset_url
templates.env.filters["comma"] = lambda value: f"{value:,}"
templates.env.filters["flag"] = country_flag
templates.env.filters["tick"] = tick_label
