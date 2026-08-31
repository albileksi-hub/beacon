"""The lockfile has to stay honest, or it is worse than not having one.

Twelve `>=` floors and no lock meant two people building the image a week apart
got different software and neither could say what changed. For self-hosted
analytics that is the whole supply chain resting on whatever PyPI served that
afternoon, with nobody watching for a compromised release.

A lock only helps while it still describes the project. These tests are what
stop it becoming a file nobody regenerates.
"""

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCK = ROOT / "requirements.lock"


def _declared() -> list[str]:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())
    return data["project"]["dependencies"]


def _pinned() -> dict[str, str]:
    """Name (extras stripped) -> version, for every == line in the lock."""
    found = {}
    for name, _extras, version in re.findall(
        r"^([A-Za-z0-9._-]+)(\[[^\]]*\])?==([^\s\\]+)", LOCK.read_text(), re.M
    ):
        found[name.lower().replace("_", "-")] = version
    return found


def test_every_declared_dependency_is_pinned():
    """A dependency added to pyproject and not locked installs unpinned.

    That is the drift a lockfile exists to prevent, and it happens quietly:
    the build still works, so nothing tells you the guarantee has gone.
    """
    pinned = _pinned()
    missing = [
        base
        for dep in _declared()
        if (base := re.split(r"[><=!\[;\s]", dep, maxsplit=1)[0].lower().replace("_", "-"))
        not in pinned
    ]

    assert not missing, f"declared in pyproject but not in requirements.lock: {missing}"


def test_every_pin_carries_a_hash():
    """--require-hashes is only as good as the file it reads.

    A pin without a hash pins the version and not the artefact, which leaves
    the thing a lockfile is mostly for -- knowing the bytes are the bytes --
    unprotected.
    """
    text = LOCK.read_text()
    blocks = re.split(r"\n(?=[A-Za-z0-9._-]+(?:\[[^\]]*\])?==)", text)
    unhashed = [
        b.splitlines()[0].strip()
        for b in blocks
        if "==" in b.split("\n")[0] and "--hash=sha256:" not in b
    ]

    assert not unhashed, f"pinned without a hash: {unhashed}"


def test_the_image_installs_from_the_lock_and_demands_hashes():
    """Wiring, not decoration. The lock does nothing unless the build reads it."""
    dockerfile = (ROOT / "Dockerfile").read_text()

    assert "requirements.lock" in dockerfile, "the image never copies the lock"
    assert "--require-hashes" in dockerfile, "the image installs without checking hashes"
