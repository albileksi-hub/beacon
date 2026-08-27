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
from typing import Any

import pytest

from app import dependencies

ROUTER_MODULES = ("auth", "dashboard", "exports", "ingest", "keys", "sites", "stats")

# Guards that establish who the caller is and what they own.
OWNERSHIP = ("OwnedSite", "RequiredUser")
READ_ACCESS = ("ReadableSite",)
ALL_GUARDS = OWNERSHIP + READ_ACCESS + ("CurrentUser", "ApiAccount")

# Routes that change something and are still open to anyone, each for a reason
# that has to survive being read aloud.
PUBLIC_BY_DESIGN = {
    ("POST", "/login"),      # cannot require a session to create one
    ("POST", "/signup"),     # likewise
    ("POST", "/logout"),     # ending a session you may not have harms nobody
    ("POST", "/api/event"),  # the collector; it runs on other people's sites
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
