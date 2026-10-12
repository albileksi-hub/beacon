# Changelog

Notable changes, newest first. Versions follow [semantic versioning](https://semver.org).

## Unreleased

- **Colour contrast is tested, not asserted.** Both themes, every text pair,
  read from the stylesheet's own tokens. The design notes had drifted two
  palettes behind and cited a tightest ratio of 5.02:1; it is 4.72:1.
- **The tracking snippet's attributes are checked against the script**, so a
  documented `data-site` where the script reads `data-site-id` fails a test
  rather than silently recording nothing.

- **A session secret shorter than 32 characters is refused.** Comparing against
  the built-in default alone missed every other placeholder, including the one
  this repository's own compose file used to substitute.

- **The lock can be regenerated.** It is a resolution, and a resolution is only
  valid for the platform it was solved on -- compiling it on Windows drops
  `uvloop`, which has no Windows wheel, so the machine this is developed on
  could not produce a lock the image installs. `pip-tools` was a declared dev
  dependency with no way to run it, and the lock's own header recorded
  `pip-compile --no-index`, which means *ignore the package index* and fails on
  the first package. A workflow now compiles it on Linux, proves it resolves
  under `--require-hashes` and comes back clean from `pip-audit`, and returns it
  as an artifact -- so the job needs no write access to the repository.

- **Four advisories closed**: `urllib3` 2.7.0 to 2.8.0 (three) and `multidict`
  6.7.1 to 7.1.0. They had sat unfixable for weeks, and CI red with them,
  because there was no way to produce a lock that contained the fixes.

- **The earlier answer to the `pydantic-core` problem was wrong, twice.**
  Ignoring it left the child behind when the parent moved; grouping it with the
  parent cannot help either, because `pydantic-core` is published ahead of the
  `pydantic` that pins it -- 2.49.0 on 9 September, the first `pydantic` wanting
  it on 8 October -- so for a month there is no parent to group with. Dependabot
  bumped it alone and the pull request carrying the `urllib3` fixes could not
  install. No setting fixes this: a resolution cannot be edited one line at a
  time. Dependabot proposes, the workflow above produces.

- **`Select` is annotated variadically.** SQLAlchemy 2.1 made it variadic, so
  `Select[Any]` stopped meaning "a select of anything" and started meaning "a
  select of exactly one column of type `Any`" -- which none of the callers are.
  The query helpers are generic over their columns now, and that carried the
  row shapes far enough for the type checker to find two places the old
  annotation had been hiding. The declared floor is `>=2.1` to match: the code
  runs on 2.0 but does not type-check there.

- **The stats API is versioned.** `/api/v1/stats/{site}` is the documented
  surface; `/api/stats/{site}` still answers but is no longer advertised. Both
  are one router mounted twice, so they cannot drift. Within `v1`, fields are
  added and never removed or retyped.
- **A registered domain must be a hostname.** `quote".example` used to be a
  valid site, and the domain reaches the CSV export's `Content-Disposition`,
  where a quote closed the filename early. Non-ASCII domains are punycoded
  rather than refused.
- **The documented backup is rehearsed on every push**, and coverage counts
  branches rather than statements.

## 0.1.0 — 2026-09-01

The first tagged version. Everything below already existed on `main`; what is
new is that there is now a name for a particular state of it.

**What it does.** Cookieless, self-hosted web analytics. Pageviews, custom
events and goals, revenue, campaigns, entry and exit pages, funnels, live
visitors, CSV export, a read-only JSON API with per-account keys, multi-site
accounts with owner/admin/viewer roles, and optional public dashboards.

**The idea it is built around.** A visitor is identified by a keyed hash of
address and user agent, with a salt that rotates at the site's own local
midnight and is deleted after two days. Nobody can re-derive yesterday's
identifiers — not with a stolen backup, and not the operator. The cost is
stated plainly rather than hidden: there is no cross-day visitor identity, and
the same person on two devices counts twice.

**Operating it.** Docker image, `docker-compose.yml` with Postgres and an nginx
front-end that rate-limits the collector, and a `render.yaml` blueprint.
Migrations run on start. A site or an account can be deleted, taking every
event, salt and aggregate with it. Backup and restore are documented in
`docs/OPERATIONS.md`.

**How it is checked.** 846 tests at 100% branch coverage, `ruff` and
`mypy --strict` clean, run against SQLite and Postgres and on both Python 3.12
and the 3.14 the image ships. Migrations are verified by applying them,
diffing against the models, and reversing them to base. Dependencies are
pinned with hashes and audited against the advisory database on every push.

### Known limitations

- **It has never run in production.** No instance of this has ingested a real
  pageview. Every design decision here is argued from first principles and
  tested in isolation; none has met traffic it did not generate itself.
- **The collector is not rate-limited by the application.** The shipped nginx
  configuration does it. An instance run without a proxy in front has no limit.
- **The site ID cannot authorise anything.** It is public by construction. The
  collector requires the reported URL to be on the registered domain, which
  stops copied snippets and ID-scraping spam, and cannot stop a caller who
  fills the URL in correctly.
- **No email digests, alerting, or two-factor authentication.**
- **Country reporting needs a GeoIP database** that is not shipped with it.
