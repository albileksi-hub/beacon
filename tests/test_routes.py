"""Every route that changes something must say who is allowed to.

The per-endpoint tests check the endpoints that exist today. This checks the
shape of the whole surface, which is the part that goes wrong later: a new POST
added without a guard is invisible to every test that does not know it exists,
and reads as perfectly ordinary in review.

Written against the resolved dependency callables rather than the annotation
text. An earlier version of this audit matched on ``str(param.annotation)`` and
reported every route as unguarded, including ones that plainly were -- FastAPI
flattens the Annotated aliases, so the names never appear.
"""

import importlib
import typing
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from app import dependencies

ROUTER_MODULES = ("auth", "dashboard", "exports", "ingest", "keys", "sites", "stats")

# Guards that establish who the caller is and what they may change.
#
# AdministeredSite is here because publishing a dashboard and setting its
# timezone are administration rather than ownership: an admin does the work on
# a site and the owner decides who is an admin. Deciding who may open the site
# at all stays behind OwnedSite.
OWNERSHIP = ("OwnedSite", "AdministeredSite", "RequiredUser")
READ_ACCESS = ("ReadableSite",)
ALL_GUARDS = OWNERSHIP + READ_ACCESS + ("CurrentUser", "ApiAccount")

# Routes that change something and are still open to anyone, each for a reason
# that has to survive being read aloud.
PUBLIC_BY_DESIGN = {
    ("POST", "/login"),      # cannot require a session to create one
    ("POST", "/signup"),     # likewise
    ("POST", "/logout"),     # ending a session you may not have harms nobody
    ("POST", "/api/event"),  # the collector; it runs on other people's sites
    # Recovery is for people who cannot sign in, so it cannot ask them to.
    # Both are throttled per requester and neither reveals whether the address
    # exists; the reset link itself is the credential.
    ("POST", "/forgot"),
    ("POST", "/reset/{token}"),
}


def _guards_by_dependency() -> dict[Any, str]:
    """Each dependency callable mapped back to the alias that wraps it."""
    found: dict[Any, str] = {}
    for name in ALL_GUARDS:
        alias = getattr(dependencies, name)
        for meta in getattr(alias, "__metadata__", ()):
            dependency = getattr(meta, "dependency", None)
            if dependency is not None:
                found[dependency] = name
    return found


def _routes() -> Iterator[tuple[str, str, set[str]]]:
    """Every route as (method, path, the guard names it resolves through)."""
    lookup = _guards_by_dependency()
    for module_name in ROUTER_MODULES:
        module = importlib.import_module(f"app.routers.{module_name}")
        for route in module.router.routes:
            hints = typing.get_type_hints(route.endpoint, include_extras=True)
            guards = {
                lookup[dependency]
                for annotation in hints.values()
                for meta in getattr(annotation, "__metadata__", ())
                if (dependency := getattr(meta, "dependency", None)) in lookup
            }
            for method in route.methods:
                if method not in ("HEAD", "OPTIONS"):
                    yield method, route.path, guards


def test_the_audit_can_see_guards_at_all() -> None:
    """Guard against the failure that made the first version of this useless.

    If the resolution below silently stops working, every assertion here
    passes vacuously and the audit reports a clean surface forever.
    """
    guarded = [path for _, path, guards in _routes() if guards]
    assert len(guarded) >= 10, f"only {len(guarded)} routes appear guarded; resolution is broken"


def test_every_mutating_route_requires_ownership_or_a_session() -> None:
    """A POST that changes state must know who is asking."""
    unguarded = sorted(
        f"{method} {path}"
        for method, path, guards in _routes()
        if method != "GET"
        and (method, path) not in PUBLIC_BY_DESIGN
        and not set(OWNERSHIP) & guards
    )
    assert not unguarded, (
        f"these routes change something without an ownership or session guard: {unguarded}. "
        "Add one, or add the route to PUBLIC_BY_DESIGN with the reason."
    )


def test_the_public_list_does_not_outlive_the_routes_on_it() -> None:
    """An exemption for a route that no longer exists is an exemption nobody reads."""
    live = {(method, path) for method, path, _ in _routes()}
    stale = sorted(f"{method} {path}" for method, path in PUBLIC_BY_DESIGN - live)
    assert not stale, f"PUBLIC_BY_DESIGN names routes that are gone: {stale}"


@pytest.mark.parametrize("prefix", ["/api/stats/{site_id}", "/sites/{site_id}"])
def test_reading_one_sites_numbers_goes_through_an_authorisation_check(prefix: str) -> None:
    """No route may read a named site without deciding whether it may.

    The dashboard page is the exception and resolves it inline instead: it
    needs to send a signed-out visitor to the login page rather than answer
    404, which the dependency cannot express. It still calls readable_site.
    """
    inline = {("GET", "/sites/{site_id}")}
    unchecked = sorted(
        f"{method} {path}"
        for method, path, guards in _routes()
        if method == "GET"
        and path.startswith(prefix)
        and (method, path) not in inline
        and not set(OWNERSHIP + READ_ACCESS) & guards
    )
    assert not unchecked, f"these read a named site with no authorisation check: {unchecked}"


def test_the_app_refuses_to_start_on_the_built_in_session_secret(monkeypatch) -> None:
    """The default is a constant in a public repository.

    An instance running on it signs session cookies with a key anybody can read
    off GitHub, so anybody could mint a cookie for any account on it. That used
    to be a log line, which is a thing nobody reads on the one morning it
    matters.
    """
    from app import main
    from app.config import Settings

    default = Settings.model_fields["session_secret"].default
    monkeypatch.setattr(
        main, "get_settings", lambda: Settings(session_secret=default)
    )

    with pytest.raises(RuntimeError, match="public repository"):
        main.create_app()


def test_a_throwaway_instance_can_still_say_so(monkeypatch) -> None:
    """The escape hatch, named so it cannot be set while meaning something else.

    run.py sets it, because somebody running the development entrypoint has
    already said what they mean. A deploy does not come through run.py.
    """
    from app import main
    from app.config import Settings

    default = Settings.model_fields["session_secret"].default
    monkeypatch.setattr(
        main,
        "get_settings",
        lambda: Settings(session_secret=default, allow_insecure_sessions=True),
    )

    assert main.create_app() is not None


def test_a_real_secret_needs_no_permission(monkeypatch) -> None:
    from app import main
    from app.config import Settings

    monkeypatch.setattr(
        main, "get_settings", lambda: Settings(session_secret="something-random-enough")
    )

    assert main.create_app() is not None


def test_the_compose_file_ships_no_session_secret_of_its_own() -> None:
    """It used to substitute one, and that walked past the guard beside it.

    `${BEACON_SESSION_SECRET:-change-me-before-deploying}` is a constant in a
    public repository that every copy of this stack would have shared, and the
    application started on it happily: the refusal added for exactly this
    compares against the one built-in default and knew nothing of a second
    string. So the documented way to run Beacon locally produced precisely the
    instance that refusal exists to prevent.

    `:?` makes compose refuse when the variable is unset, which is the same
    answer the application gives, in the place a person meets first.
    """
    compose = (Path(__file__).resolve().parent.parent / "docker-compose.yml").read_text()
    line = next(ln for ln in compose.splitlines() if "BEACON_SESSION_SECRET:" in ln)

    assert ":?" in line, f"compose must require the variable, not default it: {line}"
    assert ":-" not in line, f"compose is substituting a default again: {line}"


def test_the_compose_file_is_still_valid_yaml() -> None:
    """The first attempt at that line was not.

    The `:?` message contained a colon followed by a space, which YAML reads as
    a mapping, and `docker compose up` would have failed outright. Reading the
    line did not catch it; parsing the file did.
    """
    import yaml

    compose = yaml.safe_load(
        (Path(__file__).resolve().parent.parent / "docker-compose.yml").read_text()
    )

    assert compose["services"]["app"]["environment"]["BEACON_SESSION_SECRET"].startswith(
        "${BEACON_SESSION_SECRET:?"
    )


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def test_the_proxy_and_the_trust_setting_travel_together() -> None:
    """Either alone is worse than neither.

    The application reads X-Forwarded-For only when told to, because any client
    can set it and believing it while directly exposed lets a visitor mint a
    fresh identity per request. So a proxy without the setting means every
    request appears to come from the proxy: measured, three distinct visitors
    became one visitor_id, which empties the numbers and makes one person's
    failed login lock out everybody through a shared throttle fingerprint.

    The setting without the isolation is the other half of the same problem --
    if the application can be reached around the proxy, the header it now
    trusts is under the caller's control again. Hence no published port on the
    app service.
    """
    import yaml

    compose = yaml.safe_load((_repo_root() / "docker-compose.yml").read_text())
    app = compose["services"]["app"]
    proxy = compose["services"]["proxy"]

    assert app["environment"]["BEACON_TRUST_PROXY_HEADERS"] == "true"
    assert "ports" not in app, "the app must not be reachable around the proxy"
    assert proxy["ports"] == ["8000:80"], "localhost:8000 should still be the way in"


def test_the_collector_is_rate_limited_at_the_proxy() -> None:
    """The one endpoint open to the internet, and the app does not limit it."""
    conf = (_repo_root() / "deploy" / "nginx.conf").read_text()

    assert "limit_req_zone" in conf, "no rate limit zone is defined"
    assert "location = /api/event" in conf, "the collector is not matched exactly"
    assert "limit_req zone=collect" in conf, "the zone is defined but not applied"


def test_the_proxy_overwrites_the_forwarded_header_rather_than_appending() -> None:
    """$proxy_add_x_forwarded_for would hand visitors their own identity.

    It keeps whatever the client sent and appends the peer. The application
    reads the leftmost entry as the original visitor, so a visitor sending
    their own X-Forwarded-For would choose what they are counted as, and could
    pick a new one per request. This is the outermost proxy, so the address it
    is talking to is the truth and the client's claim is discarded.
    """
    raw = (_repo_root() / "deploy" / "forwarded.inc").read_text()
    # Directives only. The comment above them names the form being avoided,
    # and the point is which one nginx acts on.
    directives = [
        line for line in raw.splitlines() if line.strip() and not line.lstrip().startswith("#")
    ]

    assert "proxy_set_header X-Forwarded-For $remote_addr;" in "\n".join(directives)
    appending = "$proxy_add_x_forwarded_for"
    assert not [d for d in directives if appending in d], "appending lets a visitor forge it"
