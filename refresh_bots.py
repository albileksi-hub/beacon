"""Regenerate the crawler patterns from the upstream dataset.

    python refresh_bots.py

The hand-written list this replaced recognised two thirds of real crawler
user-agent strings. The third it missed included ChatGPT-User, Applebot and
Meta's fetchers -- traffic that has grown enormously and that every one of
those sites counts as a person unless something says otherwise.

The patterns are vendored rather than fetched at runtime, for three reasons:
the collector must not make a network call to decide whether an event counts,
a build should not fail because someone else's repository is down, and a
change in what counts as a bot should show up in a diff like any other change.

Source: https://github.com/monperrus/crawler-user-agents (MIT)
"""

import json
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

SOURCE = "https://raw.githubusercontent.com/monperrus/crawler-user-agents/master/crawler-user-agents.json"
TARGET = Path(__file__).resolve().parent / "app" / "services" / "bot_patterns.py"

HEADER = '''"""Crawler user-agent patterns. Generated -- do not edit by hand.

Regenerate with `python refresh_bots.py`.

Source:  {source}
Licence: MIT, Copyright (c) 2017 Martin Monperrus
Fetched: {fetched}
Count:   {count} patterns
"""

PATTERNS: tuple[str, ...] = (
'''


def main() -> int:
    print(f"fetching {SOURCE}")
    with urllib.request.urlopen(SOURCE, timeout=60) as response:  # noqa: S310
        entries = json.load(response)

    patterns = sorted({entry["pattern"] for entry in entries if entry.get("pattern")})
    print(f"  {len(entries)} entries, {len(patterns)} distinct patterns")

    body = "".join(f"    {pattern!r},\n" for pattern in patterns)
    TARGET.write_text(
        HEADER.format(
            source=SOURCE,
            fetched=datetime.now(UTC).date().isoformat(),
            count=len(patterns),
        )
        + body
        + ")\n",
        encoding="utf-8",
    )

    print(f"  wrote {TARGET.relative_to(Path.cwd())} ({TARGET.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
