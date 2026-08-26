"""Checks on the documentation itself.

The test count in the README went stale twice, and was corrected by hand twice.
A number a person has to remember to update is a number that will be wrong
again; the third correction is this file. The same goes for the claims the
README makes about files that are supposed to exist.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"


def _whole_suite_ran(config: pytest.Config) -> bool:
    """True only when this run collected everything.

    ``testscollected`` counts what this invocation collected, so running one
    file would otherwise make the comparison below fail for a reason that has
    nothing to do with the README.
    """
    # config.args is the collection targets with options already stripped out,
    # and defaults to testpaths -- ["tests"] here -- when none were given.
    targets = {Path(arg).resolve() for arg in config.args}
    selecting = config.getoption("-k", default="") or config.getoption("-m", default="")
    return not selecting and targets == {ROOT / "tests"}


def test_the_readme_states_the_real_test_count(request: pytest.FixtureRequest) -> None:
    """Whatever the README claims, it has to be what pytest just collected."""
    if not _whole_suite_ran(request.config):
        pytest.skip("only meaningful when the whole suite is collected")

    claimed = re.search(r"\*\*([\d,]+) tests, 100% coverage", README.read_text(encoding="utf-8"))
    assert claimed is not None, "the README no longer states a test count"

    stated = int(claimed.group(1).replace(",", ""))
    collected = request.session.testscollected
    assert stated == collected, (
        f"README says {stated} tests, this run collected {collected}. "
        "Update the number in README.md rather than this assertion."
    )


def test_the_readme_only_links_to_files_that_exist() -> None:
    """A dead link in the first screenful is the cheapest kind of bad look.

    Covers <img src> as well as markdown links: the cover image is an <img>
    tag, so checking only "](...)" would have left the one picture anybody
    sees first completely unguarded.
    """
    text = README.read_text(encoding="utf-8")
    targets = re.findall(r"\]\((?!https?://|#|mailto:)([^)]+)\)", text)
    targets += re.findall(r'<img[^>]+src="(?!https?://)([^"]+)"', text)
    assert targets, "expected the README to link to something"

    missing = [t for t in targets if not (ROOT / t.split("#", 1)[0]).exists()]
    assert not missing, f"README links to files that do not exist: {missing}"


def test_the_project_carries_a_licence() -> None:
    """Without one, "self-hosted alternative" is not something anyone may do."""
    licence = ROOT / "LICENSE"
    assert licence.is_file(), "no LICENSE file"
    assert "MIT License" in licence.read_text(encoding="utf-8")


def test_the_readme_stays_short_enough_to_read() -> None:
    """It was 42,000 characters once, which is nobody's idea of a summary.

    The long-form reasoning still exists, in docs/DESIGN.md. This only holds the
    front page to something a reader will actually finish.
    """
    assert len(README.read_text(encoding="utf-8")) < 6_000
    assert (ROOT / "docs" / "DESIGN.md").is_file(), "the long form should still exist"
