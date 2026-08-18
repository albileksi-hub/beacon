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

Then open http://localhost:8100/static/demo.html — an instrumented sample page.
Interactive API docs are at http://localhost:8100/docs.

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
| `BEACON_DEBUG` | `false` | Verbose errors |

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

Ingestion, enrichment and the stats API are complete and tested (96 tests,
100% coverage of `app/`). Still to come: the dashboard, multi-tenant accounts,
the rollup pipeline, and deployment.
