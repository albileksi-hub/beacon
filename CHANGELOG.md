# Changelog

Notable changes, newest first. Versions follow [semantic versioning](https://semver.org).

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

**How it is checked.** 810 tests at 100% branch coverage, `ruff` and
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
