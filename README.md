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
| Viewport width of `1437` | `Laptop` |

That last row matters more than it looks. An exact viewport width is one of
the higher-entropy signals in a browser fingerprint — "1437 pixels wide"
narrows a person down a long way, and combined with the other columns it would
undo much of the point of this project. The bucket answers the question a site
owner actually has ("do I need to care about phones?") and carries almost none
of the entropy.

Retention is enforced on a timer rather than as a side effect of traffic. A
site with no visitors for a week creates no salt for a week, so a purge that
only ran when a salt was created would leave the old ones re-derivable the
whole time — which is precisely what the rotation exists to prevent.

The schema has no column capable of holding an address, a raw User-Agent, an
exact viewport width, or a cookie ID. [`tests/test_privacy.py`](tests/test_privacy.py) asserts both facts
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

To go from an empty clone to a populated dashboard — this runs the
migrations, creates a demo account, registers the site, and generates a year of
plausible traffic:

```bash
.venv/Scripts/python.exe seed.py --days 365 --site demo.example --reset
```

```bash
.venv/Scripts/python.exe manage.py rollup --days 370
```

It prints the demo credentials it creates. The schema is only ever built by
Alembic, in every environment: creating tables straight from the models in
development and migrating in production means a model change with no migration
works locally and fails on deploy.

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
- **It follows single-page navigation** by wrapping `pushState`/`replaceState`
  and listening for `popstate`. Without that, an SPA records exactly one
  pageview per visit however much of it somebody reads. Hash-only changes are
  ignored, because that is the same page.
- **`/health` makes a database round-trip** and answers `503` when it fails. A
  check that only proves the process is running will report a container as
  healthy while its database is unreachable, which is the one situation the
  check exists to catch.
- **Bot traffic gets the same `202` a real browser gets** and is dropped
  server-side, so a crawler learns nothing about being filtered.
- **`X-Forwarded-For` is trusted only when explicitly configured.** Any client
  can set that header, so believing it while directly exposed would let a
  visitor forge a new identity per request and inflate the numbers.
- **Country lookup degrades rather than fails.** With no GeoIP database
  configured, country becomes "unknown" and ingestion continues.

## Rollups, and why unique visitors are the hard part

The dashboard issues six queries per page, and four of them are
`COUNT(DISTINCT visitor_id)` over the whole window. Against a year of events
that is a two-second page load, so the numbers are precomputed.

The catch is that **unique visitors do not add up.** One person browsing at
09:00 and again at 14:00 is one visitor that day but appears in two hourly
buckets, so summing hourly uniques overcounts. This is the problem that pushes
real analytics systems towards HyperLogLog sketches.

Beacon does not need them, because of a property that falls out of the privacy
design: **the visitor salt rotates at midnight**, so the same person browsing
on Monday and Tuesday has two unrelated IDs by construction. Cross-day
uniqueness is not merely unavailable, it is meaningless. The day is therefore
the atomic unit of visitor identity, and daily figures *are* summable into
weeks and months exactly because of it.

So the aggregates are built on the daily grain:

- `daily_stats` — per site, per day, per dimension value. Serves the summary,
  every breakdown, and the daily and monthly charts.
- `hourly_stats` — totals only, per site per hour, for the single-day view.
  Never folded upwards; that is the overcount above.

The job **rebuilds** days rather than incrementing counters. Rebuilding is
idempotent, so running it twice or resuming after a crash converges on the
same numbers, whereas a counter that double-counts once stays wrong forever
with nothing in the raw events to reveal it. It reaches back two days, because
an event can arrive after midnight for the day that just ended.

The live counter still reads raw events. It needs the last five minutes, which
no rollup grain can answer, and the `(site_id, timestamp)` index makes it cheap
regardless of table size.

### Does it work?

`app.services.stats` reads raw events and defines what a correct answer is.
`app.services.reports` reads the aggregates and is what the dashboard calls.
[`tests/test_reports.py`](tests/test_reports.py) asserts the two agree across
every period and every dimension, so the optimisation cannot quietly start
lying.

### Is it faster?

`python bench.py --site demo.example --period 12mo`, against 229,720 events on
SQLite:

| Period | Raw events | Rollups | |
| --- | --- | --- | --- |
| 30 days | 129.5 ms | 5.0 ms | **26x** |
| 12 months | 2027.1 ms | 9.6 ms | **211x** |

A two-second dashboard render becomes ten milliseconds.

## The database

Everything below was measured against a seeded year of traffic for one
mid-sized site: **4,432,316 events, 1.1 GB of SQLite**. `python analyse.py`
reproduces it — it hooks the engine, runs the real service functions, captures
every statement they issue and asks the database to explain each one, so the
plans below are what the application actually does rather than SQL retyped by
hand into a report. It exits non-zero if anything in the hot path falls back to
a full scan.

### What the reporting queries cost

| Query | Raw events | Rollups |
| --- | --- | --- |
| 30-day summary | 1546 ms | 0.62 ms |
| 12-month summary | — | 0.77 ms |
| 12-month time series | — | 3.08 ms |
| Top pages, 30 days | — | 1.09 ms |
| Live visitors (last 5 min) | 0.46 ms | reads raw, by design |

Every one is index-backed. `events` carries a single composite index on
`(site_id, timestamp, visitor_id)`, which SQLite reports as *covering* for both
the range scan and the distinct-visitor count.

There used to be a second index on `(site_id, timestamp)`. It was a strict
prefix of that one, so it could never be preferred to it, while still costing a
write on every event. Removing it measured **40% more ingest throughput with
byte-identical query plans**.

### What the collector costs

The collector commits one transaction per event, because it answers `202` on
the promise that the event is safe. That makes commit latency the whole game:

| | events/sec |
| --- | --- |
| Starting point | 140 |
| + WAL journal | 782 |
| + `synchronous=NORMAL` | 8,328 (raw insert) |
| Through the real collector path, all changes | **1,916** |

`journal_mode=WAL` stops readers blocking the writer and stops every commit
rewriting a rollback journal. `synchronous=NORMAL` under WAL is still durable
across a process crash; only an operating-system crash can lose the last few
transactions, which for pageview counts is the right trade.

Profiling the remaining time found the domain check was **30% of the cost of
ingesting one event** — a `SELECT` per event to answer a question whose answer
almost never changes. The set of registered domains is now read once per
interval instead, which took ingest from 1,118 to 1,916 events/sec.

SQLite also **ignores foreign keys unless asked**, which had quietly made every
`ON DELETE CASCADE` in the schema decorative. `PRAGMA foreign_keys=ON` is now
set on every connection, and a test deletes an account through Core SQL to
prove the cascade is real.

Postgres gets `pool_pre_ping`, because a pooled connection can be closed while
idle — by the database's own timeout, by a proxy, or by a deploy — and without
it the next request to pick that connection up fails instead of reconnecting.

### Retention

Raw events are needed for exactly two things: the live counter, which looks at
the last five minutes, and rebuilding a day's aggregates. Once a day is rolled
up and out of the refresh window, its raw rows are dead weight — and on a busy
site they are almost the entire database.

Setting `BEACON_RAW_EVENT_RETENTION_DAYS=30` against that 4.4M-event database:

```
before   4,432,316 raw events   1098.1 MB
purged   4,060,933 events older than 30 days
after      371,383 raw events     73.4 MB
```

The twelve-month report before and after: **2,482,596 visitors and 4,119,313
pageviews, both times**. Top pages identical too. A year of reporting from 7%
of the data.

Deleting is irreversible, so it is off by default and carries two guards: a
retention shorter than the refresh window is refused rather than honoured, and
a site with no aggregates is left alone — otherwise its history would go to a
job that could no longer rebuild it. Freed pages are reused by SQLite; handing
them back to the filesystem needs a `VACUUM`, which locks the database and so
stays an operator's decision rather than a background job's.

## Configuration

Every setting is an environment variable prefixed `BEACON_`:

| Variable | Default | Purpose |
| --- | --- | --- |
| `BEACON_DATABASE_URL` | `sqlite:///./beacon.db` | SQLite locally, Postgres in production |
| `BEACON_GEOIP_DB_PATH` | unset | Path to a MaxMind `GeoLite2-Country.mmdb` |
| `BEACON_TRUST_PROXY_HEADERS` | `false` | Enable only behind a proxy that overwrites `X-Forwarded-For` |
| `BEACON_SESSION_SECRET` | insecure default | Signs session cookies. Anyone holding it can forge a login |
| `BEACON_SESSION_HTTPS_ONLY` | `false` | Restrict the session cookie to HTTPS. Enable in production |
| `BEACON_ROLLUP_INTERVAL_SECONDS` | `0` | Seconds between in-process maintenance runs. `0` disables it |
| `BEACON_RAW_EVENT_RETENTION_DAYS` | `0` | Days of raw events to keep. `0` keeps them forever |
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
- **Sign-in is rate limited**, five failures per address per fifteen minutes.
  bcrypt's work factor makes guessing expensive but not impossible, and nothing
  else stands between an attacker and as many attempts as the network allows.

The rate limiter is the interesting one, because it normally works by keeping a
list of addresses — and this project promises never to store one. That promise
should not have an exception carved into it for the operator's own convenience,
so attempts are recorded against a keyed hash of the address using the same
rotating salt as the visitor IDs, with a domain-separation prefix so a login
fingerprint can never equal a visitor ID and the two tables cannot be
cross-referenced. `tests/test_privacy.py` checks the schema guard against this
table too.

## Goals

Pageviews are the default, not the limit. Anything else a site cares about is
one call:

```js
beacon("signup");
```

Two decisions in that one line:

- **The API refuses the name `pageview`.** A site should not be able to inflate
  its own view count through the same call that records a sign-up.
- **Goals do not count as pageviews.** They share a table, so counting rows
  would have quietly inflated every site's pageview figure the day it started
  tracking anything. The pageview total is now an explicit sum over pageview
  rows, and the same narrowing is applied by the rollup builder, so the
  aggregates cover exactly what the definition covers.

## Sharing a dashboard

A site is private until its owner publishes it, at which point the dashboard
and its API are readable by anyone with the link — useful for a public
transparency page, and for showing the thing to someone without asking them to
sign up first.

Publishing is resolved through ownership while reading is resolved through
visibility: anyone may read a published dashboard, but only one account decides
that it is published. An unpublished site answers `404` to a stranger rather
than `401`, so the response cannot be used to discover which domains exist here.

## The dashboard

Server-rendered Jinja2 at `/sites/{site_id}`: headline tiles with
period-over-period movement, a visitors chart, and tabbed breakdowns across
pages, sources, countries, devices, browsers and operating systems.

Almost none of it needs JavaScript:

- **The chart is inline SVG** whose geometry — points, area path, and gridlines
  on a rounded axis — is computed in Python. No charting library, and no Node
  toolchain to build one.
- **The breakdown tabs are radio inputs and sibling selectors**, so switching
  between six dimensions works with scripting turned off.
- **Growth from zero shows no percentage.** A jump from nothing is not a
  percentage increase, and rendering it as one would be a lie the dashboard
  tells every time a site starts.
- **"Today" is compared against yesterday up to the same hour**, because
  measuring a half-finished day against a whole one shows a fall every morning.

The two scripts that do exist are small: one refreshes the live visitor count
and pauses while the tab is hidden, the other toggles the theme.

Light and dark follow the system preference until someone chooses, and the
choice is applied inline in `<head>` so there is no flash of the wrong theme on
load. Static assets carry a hash of their contents in the URL, so a browser
holding yesterday's stylesheet cannot render new markup against it.

A signed-out visitor gets a page explaining what Beacon is, rather than being
dropped onto a login form with no context. Mistyped dashboard URLs render a
real error page; the API keeps answering JSON.

## The API

| Endpoint | Returns |
| --- | --- |
| `POST /api/event` | The collector. Answers `202` to everything, including bots. |
| `GET /api/stats/{site}/summary` | Visitors, pageviews, views per visitor |
| `GET /api/stats/{site}/timeseries` | One point per bucket, zero-filled |
| `GET /api/stats/{site}/breakdown/{prop}` | Top pages, sources, countries, devices, browsers, systems, screens, or goals |
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

## Running it in production

The image runs migrations on start, so a deploy that changes the schema needs
no separate step:

```bash
docker compose up --build
```

That brings up Postgres and the app together on http://localhost:8000. For a
real deployment, set these and nothing else is required:

| Variable | Why |
| --- | --- |
| `BEACON_DATABASE_URL` | `postgresql+psycopg://...` |
| `BEACON_SESSION_SECRET` | `python -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `BEACON_SESSION_HTTPS_ONLY` | `true`, once TLS is in front of the app |
| `BEACON_TRUST_PROXY_HEADERS` | `true`, but only behind a proxy that overwrites `X-Forwarded-For` |
| `BEACON_ROLLUP_INTERVAL_SECONDS` | `60`, to keep the aggregates fresh |

The container runs as a non-root user and ships a health check. `PORT` is
honoured if the host injects one.

Migrations are Alembic, and one set runs on both databases: SQLite cannot
`ALTER` most things, so batch mode rebuilds the table instead.

## What CI checks

Four jobs, on every push and pull request:

- **Lint and test (SQLite)** — `ruff`, then `mypy --strict`, then the suite
  with coverage gated at 100%. The coverage gate is deliberate: it fails a
  change that adds untested code rather than reporting a slightly lower number
  nobody looks at. The one dependency without type information is waived by
  name rather than globally, so a future untyped import gets noticed.
- **Test (Postgres)** — the entire suite again against real Postgres. Date
  truncation is the one place the dialect leaks through, and unit tests that
  compile SQL are not the same as running it.
- **Migrations match the models** — `alembic upgrade head`, then
  `alembic check`, which fails if a model was changed without a migration, and
  finally `alembic downgrade base` to prove the migration reverses.
- **Docker image builds** — builds the image, starts it, and waits for
  `/health` to answer. A Dockerfile that only builds is not evidence of much.

## Status

Feature-complete and tested: 318 tests, 100% coverage of `app/`, clean under
`mypy --strict`, running on both SQLite and Postgres in CI.

Ideas worth doing next, roughly in order of how much they would add:

- **Retention** — dropping raw events past a certain age, since the rollups
  already hold everything the dashboard needs.
- **HyperLogLog sketches**, if cross-day unique visitors were ever wanted. They
  would need the salt rotation to go, which is the entire privacy argument, so
  this is a genuine product decision rather than an engineering one.
