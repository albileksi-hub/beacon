"""The direction dependencies are allowed to point.

The layers here are conventional -- models, then services, then routers -- and
the convention held for eighty commits without anything checking it. It stopped
holding quietly: one router had begun importing a constant from a sibling
router, which is how two modules that should not know about each other end up
unable to move independently.

Read from the import statements rather than by importing the modules, so a rule
about structure is answered by the structure and not by whatever happens to be
in sys.modules at the time.
"""

import ast
from collections.abc import Iterator
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent.parent / "app"

# app.services.client takes a Request because reading the originating address
# out of one is the entire job. Naming it here is the point: the exception is
# visible, and a second service reaching for FastAPI has to be argued for
# rather than merely committed.
SERVICES_ALLOWED_FASTAPI = {"client"}


def _imports(path: Path) -> Iterator[str]:
    """Every module this file imports, by dotted name."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            yield node.module


def _modules(package: str) -> Iterator[tuple[Path, str]]:
    for path in sorted((APP / package).glob("*.py")):
        if path.name != "__init__.py":
            yield path, path.stem


def test_no_service_knows_about_a_router() -> None:
    """A service that imports a router cannot be used by anything else."""
    offenders = [
        f"app/services/{name}.py imports {imported}"
        for path, name in _modules("services")
        for imported in _imports(path)
        if imported.startswith("app.routers")
    ]
    assert not offenders, offenders


def test_no_router_imports_another_router() -> None:
    """Siblings share through the layer below them, not across.

    This is the rule that had already been broken: sites.py imported
    PERIOD_LABELS from dashboard.py, so the page listing sites could not be
    understood, moved or tested without the dashboard coming with it. The
    labels now sit beside the Period they describe.
    """
    offenders = [
        f"app/routers/{name}.py imports {imported}"
        for path, name in _modules("routers")
        for imported in _imports(path)
        if imported.startswith("app.routers")
    ]
    assert not offenders, offenders


def test_only_the_named_service_knows_about_fastapi() -> None:
    """Everything else in services is callable without a request in hand.

    That is what lets the whole of stats, rollups and erasure be tested by
    calling them, and what would stop being true one convenient import at a
    time.
    """
    offenders = [
        f"app/services/{name}.py imports {imported}"
        for path, name in _modules("services")
        if name not in SERVICES_ALLOWED_FASTAPI
        for imported in _imports(path)
        if imported.split(".")[0] in {"fastapi", "starlette"}
    ]
    assert not offenders, offenders


def test_the_exception_list_names_something_real() -> None:
    """An exemption for a module that has been renamed protects nothing."""
    present = {name for _, name in _modules("services")}
    assert present >= SERVICES_ALLOWED_FASTAPI


def test_models_depend_on_nothing_above_them() -> None:
    """The schema is the bottom of the stack; anything it imports is beneath it."""
    offenders = [
        imported
        for imported in _imports(APP / "models.py")
        if imported.startswith(("app.services", "app.routers"))
    ]
    assert not offenders, offenders


@pytest.mark.parametrize("package", ["services", "routers"])
def test_the_reader_can_see_imports_at_all(package: str) -> None:
    """Guard against the assertions above passing because nothing was read.

    If the parsing silently returned nothing, every rule here would hold
    forever and say nothing about the code.
    """
    seen = [imported for path, _ in _modules(package) for imported in _imports(path)]
    assert len(seen) > 20, f"only {len(seen)} imports found in app/{package}; the reader is broken"
