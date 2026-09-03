"""Checks on the documentation itself.

The test count in the README went stale twice, and was corrected by hand twice.
A number a person has to remember to update is a number that will be wrong
again; the third correction is this file. The same goes for the claims the
README makes about files that are supposed to exist.
"""

import gzip
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

    # "branch" is required, not optional. The gate measures branches now, and a
    # README claiming plain coverage would be describing a weaker check than
    # the one that runs. Changing the wording without changing this is how the
    # sentence and the build drift apart -- which is what happened when the
    # word was added: this assertion is the thing that noticed.
    claimed = re.search(
        r"\*\*([\d,]+) tests, 100% branch coverage", README.read_text(encoding="utf-8")
    )
    assert claimed is not None, "the README no longer states a test count and branch coverage"

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


def test_the_readme_states_the_real_script_size() -> None:
    """The other number a person had to remember, and did not.

    The README said 1.9 KB long after the script had grown to 2.3 -- it gained
    revenue handling and nothing recalculated the claim. It is the same failure
    the test count above exists to prevent, so it gets the same treatment.

    Compared within a tolerance rather than exactly: the byte count depends on
    the zlib the test happens to run against, and this suite runs on more than
    one machine. Five per cent is far tighter than the 23% the claim had
    already drifted by, and loose enough not to fail for a library version.
    """
    claimed = re.search(r"\*\*([\d.]+) KB gzipped\*\*", README.read_text(encoding="utf-8"))
    assert claimed is not None, "the README no longer states the script size"

    script = (ROOT / "static" / "beacon.js").read_bytes()
    actual_kb = len(gzip.compress(script, 9)) / 1024

    assert abs(float(claimed.group(1)) - actual_kb) / actual_kb < 0.05, (
        f"README says {claimed.group(1)} KB, the script gzips to {actual_kb:.2f} KB. "
        "Update the number in README.md rather than this assertion."
    )


def test_every_repository_link_in_the_readme_points_at_something_real():
    """The README linked [MIT](LICENSE) and there was no LICENSE.

    On a public repository that is not a broken link, it is a licensing
    statement with nothing behind it: with no file, the default is that nobody
    may use, copy or modify any of this -- the opposite of what the sentence
    says and of what a self-hosted analytics tool is for. GitHub agreed, and
    reported the repository as having no licence at all.

    Checked for every relative link rather than that one, so the next file
    promised and not added is caught the same way.
    """
    root = Path(__file__).resolve().parent.parent
    readme = (root / "README.md").read_text()

    targets = [
        target
        for _text, target in re.findall(r"\[([^\]]+)\]\(([^)]+)\)", readme)
        if not target.startswith(("http://", "https://", "#", "mailto:"))
    ]
    missing = sorted({t for t in targets if not (root / t.split("#")[0]).exists()})

    assert not missing, f"the README links to files that do not exist: {missing}"


def test_the_changelog_names_the_version_being_shipped():
    """The version in pyproject and the newest changelog heading must agree.

    A tag whose changelog entry describes a different version is how a release
    stops being a record of anything. Checked the same way the test count is:
    by reading the file rather than trusting anyone to remember.
    """
    import re
    import tomllib

    root = Path(__file__).resolve().parent.parent
    declared = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
    headings = re.findall(r"^## (\d+\.\d+\.\d+)", (root / "CHANGELOG.md").read_text(), re.M)

    assert headings, "the changelog has no version headings"
    assert headings[0] == declared, (
        f"pyproject says {declared}, the newest changelog entry says {headings[0]}"
    )


def test_no_markdown_file_links_to_something_that_is_not_there():
    """The README was checked; nothing else was.

    Written after a changelog entry pointed at `docs/RUNBOOK.md`, which has
    never existed -- the file is `docs/OPERATIONS.md`. Nothing would have
    caught it, because the only link test read one file. A rule about links
    should be a rule about links, not a rule about the README.
    """
    import re

    root = Path(__file__).resolve().parent.parent
    sources = [root / "README.md", root / "CHANGELOG.md", root / "SECURITY.md"]
    sources += sorted((root / "docs").glob("*.md"))

    broken = []
    for path in sources:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        targets = re.findall(r"\]\((?!https?://|#|mailto:)([^)]+)\)", text)
        targets += re.findall(r'<img[^>]+src="(?!https?://)([^"]+)"', text)
        for target in targets:
            resolved = (path.parent / target.split("#", 1)[0]).resolve()
            if not resolved.exists():
                broken.append(f"{path.relative_to(root)} -> {target}")

    assert not broken, f"links pointing at nothing: {broken}"
