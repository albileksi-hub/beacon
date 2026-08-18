# Beacon

Privacy-first, cookieless web analytics. A self-hosted alternative to Google
Analytics that answers the questions a site owner actually has — how many
people came, what they read, and where they came from — without collecting
anything that identifies them.

Add one line to a page and you get numbers:

```html
<script src="https://your-host/static/beacon.js" data-site-id="yoursite.com" defer></script>
```

No cookies. No consent banner. No personal data at rest.

## Why it is built this way

Most analytics tools identify visitors with a cookie, which is what makes them
a consent problem under GDPR and ePrivacy. Beacon identifies a visitor with a
**keyed hash of their address, User-Agent and site**, using a random salt that
is generated fresh every day and **deleted two days later**.

That deletion is the whole design. While today's salt exists, repeat visits
within the day are recognised. Once it is gone, nobody can reproduce the
mapping from an address to a visitor ID — not an attacker holding a stolen
database backup, and not the operator. The identifiers become genuinely
anonymous rather than merely pseudonymous, which is the distinction the
regulation turns on.

The trade is deliberate and total: there is no cross-day visitor tracking, and
the same person on two devices counts twice. That is the cost of not needing a
cookie banner.

Everything else follows the same rule — reduce at the boundary, never store and
filter later:

| Arrives in the request | What is stored |
| --- | --- |
| `https://shop.com/checkout?email=a@b.com` | `/checkout` |
| `https://mail.example/inbox?user=a@b.com` | `mail.example` |
| Full `User-Agent` string | `Chrome` / `macOS` / `desktop` |
| IP address | two-letter country code, then discarded |

The schema has no column capable of holding an address, a raw User-Agent, or a
cookie ID. [`tests/test_privacy.py`](tests/test_privacy.py) asserts both facts
against a live request, so the claims above fail the build if they stop being
true.

## Running it

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e ".[dev]"
.venv/Scripts/python.exe run.py
```

Then open http://localhost:8100 for the dashboard, or
http://localhost:8100/static/demo.html for an instrumented sample page.
Interactive API docs are at http://localhost:8100/docs.

To fill the dashboard with plausible traffic:

```bash
.venv/Scripts/python.exe seed.py --days 30 --site demo.example
```

Tests:

```bash
.venv/Scripts/python.exe -m pytest --cov=app --cov-report=term-missing
```

## How it fits together

```
browser ──POST /api/event──► FastAPI ──► enrichment ──► SQLite/Postgres ──► dashboard
                                          │
                                          ├─ user_agent.py   Chrome / macOS / desktop, bot detection
                                          ├─ referrers.py    google.de → "Google", self-referrals → Direct
                                          ├─ geo.py          IP → country, then forgotten
                                          └─ visitors.py     daily-salted, site-scoped visitor hash
```

Notes on a few decisions:

- **The collector answers `202`, not `201`.** The visitor's browser gets an
  immediate acknowledgement and never waits on storage. Analytics must never
  slow down the page it measures.
- **The tracking script uses `navigator.sendBeacon`**, which survives page
  unload, falling back to `fetch(keepalive)`. It honours Do Not Track and fails
  silently — analytics should never break someone's site.
- **Bot traffic gets the same `202` a real browser gets** and is dropped
  server-side, so a crawler learns nothing about being filtered.
- **`X-Forwarded-For` is trusted only when explicitly configured.** Any client
  can set that header, so believing it while directly exposed would let a
  visitor forge a new identity per request and inflate the numbers.
- **Country lookup degrades rather than fails.** With no GeoIP database
  configured, country becomes "unknown" and ingestion continues.

## Configuration

Every setting is an environment variable prefixed `BEACON_`:

| Variable | Default | Purpose |
| --- | --- | --- |
| `BEACON_DATABASE_URL` | `sqlite:///./beacon.db` | SQLite locally, Postgres in production |
| `BEACON_GEOIP_DB_PATH` | unset | Path to a MaxMind `GeoLite2-Country.mmdb` |
| `BEACON_TRUST_PROXY_HEADERS` | `false` | Enable only behind a proxy that overwrites `X-Forwarded-For` |
| `BEACON_SESSION_SECRET` | insecure default | Signs session cookies. Anyone holding it can forge a login |
| `BEACON_SESSION_HTTPS_ONLY` | `false` | Restrict the session cookie to HTTPS. Enable in production |
| `BEACON_DEBUG` | `false` | Verbose errors |

## Accounts and tenancy

Sign up, add a domain, get a snippet. A domain belongs to exactly one account,
and every query is scoped to it.

Some choices that are easy to get wrong:

- **A site owned by someone else is a 404, not a 403.** A 403 confirms the
  domain exists on the platform, which is enough to enumerate the customer
  list one guess at a time.
- **Login failures never say which half was wrong.** An unknown address is
  also verified against a decoy hash, so a missing account and a wrong
  password take the same time -- otherwise the response time alone reveals
  which addresses are registered.
- **The collector ignores unregistered domains**, and answers them with the
  same `202` everything else gets. Without the check it is an open write
  endpoint; with a different response it becomes a way to probe which sites
  are tracked here.
- **bcrypt rejects passwords past its 72-byte limit rather than truncating.**
  Silent truncation makes a long passphrase weaker than the person choosing it
  believes.
- **Sessions are signed cookies with `SameSite=Lax`**, which is what stands in
  for CSRF tokens on these forms, and the session is cleared before login to
  rule out fixation.

## The dashboard

Server-rendered Jinja2 at `/sites/{site_id}`: headline tiles, a visitors
chart, and top-N panels for pages, sources, countries and devices.

The chart is inline SVG whose geometry is computed in Python, so the page
renders without JavaScript and the project needs no charting library — and no
Node toolchain to build one. The only script on the page keeps the live
visitor count fresh, and it pauses while the tab is hidden rather than polling
a page nobody is reading.

Light and dark themes follow the system preference. The layout collapses to a
single column on a phone.

## The API

| Endpoint | Returns |
| --- | --- |
| `POST /api/event` | The collector. Answers `202` to everything, including bots. |
| `GET /api/stats/{site}/summary` | Visitors, pageviews, views per visitor |
| `GET /api/stats/{site}/timeseries` | One point per bucket, zero-filled |
| `GET /api/stats/{site}/breakdown/{prop}` | Top pages, sources, countries, devices, browsers, or operating systems |
| `GET /api/stats/{site}/live` | Visitors in the last five minutes |

`period` accepts `today`, `7d`, `30d`, `6mo`, `12mo`. Bucket size follows from
the period rather than being chosen by the caller -- a year of hourly buckets
is 8,760 points, which is neither readable on a chart nor cheap to compute.

Two details worth pointing at:

- **Empty buckets are returned, not omitted.** A chart with holes in it reads
  as broken, so the series is zero-filled in Python against the full list of
  buckets the range covers.
- **`{prop}` is an enum, not a column name.** The request parameter never
  reaches SQL; it selects from a whitelist. `breakdown/passwords` is a `422`.

Date truncation is the one place the database dialect leaks through --
SQLite's `strftime` and Postgres's `date_trunc` have nothing in common. It is
isolated to a single function, and because Postgres never runs in the test
suite, a test compiles the Postgres expression and asserts on the generated SQL
so a dialect typo cannot reach production unnoticed.

## Status

Ingestion, enrichment, the stats API, the dashboard and multi-tenant
accounts are complete and tested (152 tests, 100% coverage of `app/`). Still
to come: the rollup pipeline, and deployment.
