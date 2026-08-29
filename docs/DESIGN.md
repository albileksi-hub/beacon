# Design notes

The reasoning behind Beacon, kept out of the [README](../README.md) so that the
README stays a thing you can read in a minute. Nothing here is required to run
the project; all of it is required to argue with it.

Every measurement below was taken on the machine described beside it, against
the seeded dataset, and can be reproduced with the command quoted above it.

---

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

### What "no consent banner" does and does not mean

The cookie-consent rule in ePrivacy Article 5(3) is about storing or reading
things on someone's device. Beacon does neither, so on the usual reading it
does not trigger that rule, and no consent banner is required for it.

That is a narrower statement than "compliant", and the difference matters:

- **Exemption from consent is not exemption from telling people.** Regulators
  that exempt audience measurement still expect a site to disclose it and to
  offer a way to refuse. Beacon reads `localStorage.beacon_ignore === "true"`
  and stays silent when it is set — a site puts its own control in front of
  that flag. The script only ever *reads* it; writing is the site's job, which
  is what keeps the script itself free of device storage.
- **The conditions are cumulative.** The exemption assumes measurement for the
  publisher alone, aggregate statistics, no combining with other datasets, and
  no tracking across sites. Beacon's hashes are salted per site, so the same
  person on two customers' sites is uncorrelatable — but an operator who
  exports the data and joins it to something else has left the exemption
  behind, and no amount of design here can stop that.
- **National rules differ and move.** This is a description of the design, not
  legal advice, and it is not a substitute for someone qualified looking at how
  a particular site uses it.

Everything else follows the same rule — reduce at the boundary, never store and
filter later:

| Arrives in the request | What is stored |
| --- | --- |
| `https://shop.com/checkout?email=a@b.com` | `/checkout` |
| `https://mail.example/inbox?user=a@b.com` | `mail.example` |
| `?utm_source=hn&email=a@b.com` | `hn`; the rest of the query is never read |
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
source .venv/bin/activate  # Windows: .venv\Scripts\activate
python -m pip install -e ".[dev]"
python run.py
```

Then open http://localhost:8100 for the dashboard, or
http://localhost:8100/static/demo.html for an instrumented sample page.
Interactive API docs are at http://localhost:8100/docs.

To go from an empty clone to a populated dashboard — this runs the
migrations, creates a demo account, registers the site, and generates a year of
plausible traffic:

```bash
python seed.py --days 365 --site demo.example --reset
```

```bash
python manage.py rollup --days 370
```

It prints the demo credentials it creates. The schema is only ever built by
Alembic, in every environment: creating tables straight from the models in
development and migrating in production means a model change with no migration
works locally and fails on deploy.

Tests:

```bash
python -m pytest --cov=app --cov-report=term-missing
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
- **Country lookup degrades rather than fails.** Not only when no GeoIP
  database is configured, which was the easy half. A path that is not a file, or
  a file that is not a database — a truncated download, an update interrupted
  halfway — used to raise out of the resolver, and the collector asks for that
  resolver on every event. `lru_cache` does not cache an exception, so it would
  not have failed once: it would have failed every request from then on, for the
  least important column on the row. Every one of those cases now falls back to
  "unknown" and says so in the log, because silently reporting every visitor as
  unknown is the kind of thing somebody notices a month later.

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
mid-sized site: **4,465,603 events, 1.1 GB of SQLite**. `python analyse.py`
reproduces it — it hooks the engine, runs the real service functions, captures
every statement they issue and asks the database to explain each one, so the
plans below are what the application actually does rather than SQL retyped by
hand into a report. It exits non-zero if anything in the hot path falls back to
a full scan.

### What the reporting queries cost

| Query | Raw events | Rollups |
| --- | --- | --- |
| 30-day summary | 997 ms | 0.74 ms |
| 12-month summary | — | 0.90 ms |
| 12-month time series | — | 2.85 ms |
| Top pages, 30 days | — | 0.95 ms |
| Live visitors (last 5 min) | 0.54 ms | reads raw, by design |
| Is this domain registered | — | 0.01 ms, no SQL at all |

Two of those moved when days became local. Grouping on a stored, indexed `day`
beats truncating a timestamp: rebuilding a year of rollups went from **120s to
49s**, and the raw 30-day summary from 1546 ms to 997 ms. The registry cache
means the collector's domain check now issues no statement whatsoever.

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
| Through the real collector path, all changes | **2,022** |

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

A third guard sits on the other side of it. A rebuild deletes a day's
aggregates before recomputing them, so running the backfill above — `manage.py
rollup --days 400`, which this document recommends — against an instance with
retention enabled, which it also recommends, used to delete every aggregate it
could no longer rebuild. On a seeded database that was 69% of the history, in
one command, with no error and a cheerful `rebuilt 400 site-days`.

So retention now records how far back it has taken events, in
`sites.raw_events_purged_through`, and a rebuild refuses any day at or before
it and says how many it left alone. The mark is recorded rather than inferred
from what survives, because "this site has no raw events" is also true of a
site whose events were removed for some other reason — spam, a bad deploy, a
test run — and those aggregates are simply wrong and *should* be rebuilt away.
Only retention makes them the last copy, so only retention says so.

### Under concurrent load

Everything above is the database path called from one thread. `python
loadtest.py --events 4000 --workers 32` drives the actual endpoint, which is
the only number that describes what the service can take.

One transaction per event, 32 connections:

```
throughput   472 requests/sec
p99          915 ms
max        4,249 ms
```

No failures — but a tracking beacon with a four-second worst case is a tracking
beacon that slows down the page it is measuring. SQLite permits exactly one
writer, so those requests were queueing on a lock while holding an HTTP
connection open.

Setting `BEACON_INGEST_BUFFER_SIZE` moves the write off the request path: the
endpoint hands the event to a bounded queue and answers immediately, and one
thread drains it in batches. Same load:

```
throughput   565 requests/sec
p99           91 ms      (10x better)
max          135 ms      (31x better)
```

The trade is stated where it lives, in `app/services/collector.py`: `202` now
means the event was accepted rather than committed, so a process killed with
events still queued loses them. That is right for pageview counts and wrong for
anything that must not be lost, which is why it is off by default. A clean
shutdown drains the queue. The queue is bounded because dropping events under a
flood and counting the drops is survivable, while growing a list until the
kernel intervenes is not — and `/health` reports both the depth and the drops,
since silent loss is the one failure this design can have.

Throughput barely moved, and that is the more interesting result. Profiling
showed enrichment costs about 20µs per event, and an event for an *unregistered*
domain — which does essentially nothing and returns — still only manages 742
requests/sec on this machine, against 698 for `GET /health`. The collector at
565 is within a quarter of a do-nothing request. The ceiling is the HTTP stack
on one Windows uvicorn worker, not this code, and the honest way to raise it is
more workers and a database that does not serialise writers.

## Logs and exports

### The access log is a privacy surface too

The default request log in most stacks carries the client address and the full
URL including its query string — precisely the two things this project strips
out of everything it stores. Logging them would put back what the product
promises to discard, so neither appears:

```json
{"time": "2026-08-19T22:34:00+0200", "level": "info", "logger": "beacon.request",
 "message": "request", "request_id": "b8190fc57e1f4aff", "method": "GET",
 "path": "/login", "status": 200, "duration_ms": 8.11}
```

That line is from a request to `/login?email=someone@example.com&token=s3cr3t`.
A test asserts the address and the query never reach it.

Every response carries an `X-Request-Id`, and one supplied upstream is honoured
so a request stays traceable across whatever sits in front of the service.
Health and static traffic log at debug, because an orchestrator's probes
otherwise drown out everything real. `BEACON_LOG_JSON=true` switches to one
object per line for anywhere logs are shipped rather than read.

It is raw ASGI middleware rather than Starlette's `BaseHTTPMiddleware`, which
buffers responses and would defeat the streaming export below.

### Taking the data out

`GET /sites/{site}/export.csv?period=12mo` streams the site's aggregates:

```
day,dimension,value,visitors,pageviews,bounces,revenue_minor
2026-07-21,page,/,152,186
2026-08-19,event,add-to-basket,1,0
```

Exporting the daily grain rather than raw events means the file is bounded by
days times dimensions rather than by traffic, and it is the same export whether
or not retention has run. It is streamed a chunk at a time, because building a
busy site's export in memory is a way to run the process out of it, and it goes
through the `csv` module rather than joining with commas — a pathname can
contain a comma or a quote, and hand-rolled CSV is how an export quietly
corrupts itself.

Resolved through the same visibility rule as the dashboard: a published site's
numbers are already served over the API, so refusing the same numbers in a
different shape would be theatre rather than a control.

## Hardening

Four things found by probing the running service rather than by reading it:

- **Blank event names were accepted and stored.** `name=""` and `name="   "`
  both answered `202` and became rows, which would have shown up in the goals
  report as empty entries. Names are now trimmed and required.
- **A 5MB request body was read and parsed in full before validation rejected
  it** — 5MB of memory per concurrent connection, which is an inexpensive way
  to push a process over from the outside. Oversized bodies are now refused on
  the `Content-Length` header, before a byte is read; a body that declares no
  length is counted as it arrives and the connection is dropped.
- **The payload capped `site_id` at 64 characters while the column allows 253**,
  so a domain between those two lengths could be registered and could then
  never send an event.
- **No security headers.** Responses now carry `nosniff`, `X-Frame-Options:
  DENY`, `Referrer-Policy: same-origin` and a content security policy.

`Referrer-Policy` is the one that matters most here: a dashboard URL contains
the customer's domain, so without it, following a link off the page hands that
domain to whoever is on the other end — which on this project of all projects
would be embarrassing.

The policy keeps `script-src 'self'`, which meant moving the theme bootstrap
out of the page into a file; it has to run before first paint, so it is a
blocking same-origin script rather than an inline one. `style-src` still allows
inline, because the breakdown bars carry their width as an inline style — a
percentage of a number the server computed, never anything a visitor supplied.

Also refused: a domain longer than the column allows. SQLite ignores `VARCHAR`
lengths, so without an explicit check that is accepted in development and
rejected in production.

## Whose day is it?

A day was the atomic unit of this system before it was a feature: it is the
grain the aggregates are built on, and the interval the visitor salt rotates
with. It was also, until recently, always a **UTC** day — so an owner in Berlin
read days beginning at 01:00 their time, and an owner in Los Angeles read days
that began at four the previous afternoon.

Each site now carries its own zone, and two decisions follow from that. They
are the whole design:

**The bucket is computed once, at ingest, in the site's zone.** The event
stores the local day and hour it belongs to, so no query ever truncates a
timestamp. That removed the single place the database dialect leaked into the
reporting SQL — SQLite's `strftime` and Postgres's `date_trunc` have nothing in
common — and a test now fails the build if `func.strftime`, `func.date_trunc`
or `func.to_char` reappears anywhere in `app/`.

**The salt rotates at the site's local midnight, not at UTC midnight.** This is
the part that is easy to get wrong. Daily figures sum into weeks and months
precisely because a visitor cannot be recognised across a rotation. Had the
salt kept turning over at 02:00 Berlin time, somebody browsing either side of
that hour would hold two identities inside a single Berlin day, and every
weekly total would have drifted quietly above the truth. Salts are therefore
per site as well as per day.

[`tests/test_local_days.py`](tests/test_local_days.py) is written around exactly
that case: two events at 23:30 and 00:30 UTC — 01:30 and 02:30 in Berlin, the
same Berlin day — land on one day, resolve to one visitor, and roll up as one
visitor with two pageviews.

Changing a site's zone only affects events from then on. Days already
aggregated keep the boundaries they were built with, because the raw events
behind them may well have been deleted by retention.

### A local day is not always 24 hours

Twice a year it is 23 or 25, and the hourly chart used to assume otherwise. It
walked the wall clock — midnight, one, two — which on the morning the clocks go
forward drew an `02:00` bar for an hour that does not exist and that no event
could ever land in, because ingest derives the hour by *converting* the instant
rather than by counting from midnight.

Buckets are now walked in absolute time and converted back, so one exists
exactly when an event could fall in it: 23 on the short day, 24 on an ordinary
one. The long day is the more interesting half. It has 25 hours but only 24
distinct hour values, because both 02:00s are stored as hour 2 — so the labels
are deduplicated and that single bar holds both. That is not a rounding error
but the grain: the events really do say the same thing.

None of this ever touched the daily figures. A 25-hour day counts all 25 hours,
because the day an event belongs to comes from the same conversion — which is
why the salt rotation and the summability argument were never at risk.

## Campaigns, downloads, and what was left out

Reading what the established privacy-first tools do — Plausible, Umami,
GoatCounter — turned up one real gap and a couple of cheap wins.

**Campaign tags were being thrown away.** Every one of those tools reports
`utm_source`, `utm_medium` and `utm_campaign`; this one discarded the whole
query string, tags included. It now reads exactly those three parameters by
name and still drops everything else in the query unread — a stricter position
than storing the query and filtering it later, and the same argument
`pathname_of` already makes. A campaign tag is not personal either:
`utm_campaign=spring-sale` describes the link, not whoever clicked it.

A tag beats the referrer when both are present, because the tag is a
deliberate statement about where a visit came from and a referrer is an
accident of how someone arrived. Both new breakdowns filter out untagged
traffic — a "(none)" row would otherwise dwarf every real campaign on every
site — which the existing `BREAKDOWN_FILTERS` mechanism already supported.

**File downloads are tracked automatically.** A download is same-origin by
definition, so its path is a path on the site like any other and it needs no
schema this project does not already have. A site serving its own error page
can add `data-404` to the script tag.

**Outbound link clicks are deliberately not tracked.** The useful part of an
outbound click is *where it went*, and there is nowhere in this schema to put a
destination host. Adding a JSON properties column would do it, and would put
dialect-specific SQL — SQLite's `json_extract` against Postgres's `->>` — back
into the reporting layer, which is exactly what the timezone work removed. It
is a real feature and it is missing on purpose; the note is here so the gap
reads as a decision rather than an oversight.

The tracking script is **2,148 bytes gzipped** (`gzip -9`), comments and all —
it is served as written rather than minified, so the reasoning travels with it.

## Telling crawlers from people

Bot filtering was a list of about sixteen substrings written from memory. Tested
against 2,118 real crawler user-agent strings from a maintained dataset, it
recognised **65.5%** of them. The third it missed included `ChatGPT-User`,
`Applebot`, `Bytespider` and Meta's fetchers — traffic that barely existed when
most such lists were written, and that every site running one of them is
counting as people.

The patterns now come from
[monperrus/crawler-user-agents](https://github.com/monperrus/crawler-user-agents)
(MIT), vendored by `python refresh_bots.py` rather than fetched at runtime: the
collector should not make a network call to decide whether an event counts, a
build should not fail because someone else's repository is down, and a change
in what counts as a bot should show up in a diff like any other change.

**1,500 patterns, 100% of those strings recognised, and no real browser
misclassified** — including the Cubot phone that a naive `"bot" in ua` check
eats.

Doing it naively cost 625µs per call against a browser string, which would have
made bot detection the slowest thing in the request. Two changes fixed that:

- **Literals are matched by substring, not by the regex engine.** Two thirds of
  the dataset is plain text with no metacharacters, and pulling those out of the
  alternation took a cold call from 625µs to 273µs.
- **Answers are cached.** A few thousand distinct strings account for almost all
  real traffic, so a warm call is **0.09µs** — a hundred times quicker than the
  sixteen-substring version, because the cache also skips parsing. The cache is
  bounded, since the header is supplied by the caller.

The collector also now rejects unregistered domains *before* touching the user
agent. There is no reason to match 1,500 patterns against traffic aimed at a
site nobody registered.

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
| `BEACON_INGEST_BUFFER_SIZE` | `0` | Events to buffer before batching. `0` commits each one separately |
| `BEACON_INGEST_BATCH_SIZE` | `500` | Maximum events per write |
| `BEACON_INGEST_FLUSH_SECONDS` | `0.25` | How long a partial batch waits |
| `BEACON_LOG_LEVEL` | `INFO` | Root log level |
| `BEACON_LOG_JSON` | `false` | One JSON object per line instead of human-readable text |
| `BEACON_MAX_REQUEST_BYTES` | `65536` | Largest request body the service will read |
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

## People, and what a role is for

A site used to belong to `sites.owner_id` and nobody else, which made a
dashboard something exactly one person could ever open. That is fine for one
author and useless the moment a colleague needs the numbers.

Access is now a row in `site_members`, and the owner has one too. That last
part is the whole reason the change is small: every check asks the same
question of the same table instead of special-casing whoever created the site.
`sites.owner_id` stays as the record of who registered the domain, which is
also what keeps one domain to one account.

Three roles, drawn along one line — doing the work, versus handing out the
keys:

| | Read | Publish, timezone | Decide who gets in |
| --- | --- | --- | --- |
| **owner** | yes | yes | yes |
| **admin** | yes | yes | no |
| **viewer** | yes | no | no |

So publishing resolves through a new `AdministeredSite` rather than
`OwnedSite`: an admin does the work on a site, and the owner decides who is an
admin. `tests/test_routes.py` had to be told about that guard, which is the
audit doing its job — a new dependency on a mutating route is exactly what it
exists to notice.

Two refusals are worth stating, because an interface that merely declines to
offer them is not a rule:

- **A site has one owner.** Adding a second, or promoting somebody to owner,
  is refused rather than quietly making "who owns this" a question with two
  answers.
- **The owner cannot be removed or demoted, including by themselves.** A site
  with no owner is one nobody can publish, rename or delete, and nothing else
  in the system would notice it had happened.

The migration backfills an owner membership for every existing site. Without
that line the upgrade takes every dashboard away from the person who made it,
so it is checked directly: build the schema at the previous revision, insert a
site, upgrade, and assert the owner still has it.

What is deliberately missing is the invite. Granting access needs an address
that already has an account, because an invitation means issuing a token and
sending mail, and there is no mail in this project yet. An unknown address is
refused with a message that says so — which is better than a half-built invite
flow, whose actual failure mode is appearing to work and doing nothing.

## Revenue, and never touching a float

An event can carry what it was worth:

```js
beacon("purchase", { revenue: 49.90 });
```

Money is the one number here that cannot be approximately right, so it is
integers end to end. The column holds minor units -- 4990 for 49.90 -- because
a float cannot represent most prices and a `NUMERIC` is a real decimal on
Postgres and a float on SQLite, which is the dialect leaking into the one place
it must not.

The amount also travels as a string. A JSON number is a double, and 0.29
arrives as 28.999999999999996; truncating that loses a penny. It is not an edge
case: of every price up to 199.99, 1,145 of them -- 5.7% -- come out a penny
short if a float touches them. `tests/test_revenue.py` counts them, so the
reason survives somebody deciding a float would be simpler.

Rounding is half-up rather than Python's default half-to-even, which turns
0.005 into 0.00. Correct for statistics, wrong for a till.

Because the rollup builder already groups by every dimension, adding one
`SUM(revenue_minor)` gives revenue per source, per campaign, per country and
per landing page without a single extra query. Two exceptions, both honest
zeroes rather than accidental numbers:

- **Entry and exit pages carry no revenue.** They are derived from the
  pageviews of a visit, and a purchase is a custom event, so attributing money
  to them would mean a different grouping rather than a wider select.
- **There is no currency conversion.** One currency per site. A rate means
  either a network call on the ingest path -- which the collector must never
  make -- or a table that is quietly out of date, and both are worse than
  reporting the number the site actually sent. Plausible converts to a base
  currency and warns that mixing currencies in one goal sums them as though
  they were the same; this refuses the ambiguity instead by having one per
  site.

## Any window, not just the five on offer

`?from=2026-07-01&to=2026-07-21` on the dashboard, the stats API and the CSV
export. Every other tool in this category has had this for years; it was
missing here for no better reason than that five buttons were easier to build.

It cost almost nothing, and the reason is the daily grain. The aggregates are
keyed by day, so an arbitrary window is a sum over a different set of days --
the same query the named periods already run. Nothing in the reporting layer
changed at all.

Three decisions worth keeping:

**The bucket follows the length of the window**, using the thresholds the named
periods already imply: a single day by the hour, up to a quarter by the day,
longer than that by the month. A year of hourly buckets is 8,760 points, which
is neither readable on a chart nor cheap to compute.

**Half a range is an error, not a default.** A request carrying `from` without
`to` could quietly be read as a month, and the caller would never learn their
link was wrong. It is refused by name instead.

**The dashboard shows a bad range; the API refuses one.** A 422 on the page
would be a blank screen for a typo in a URL somebody pasted, so the page comes
back on the default period with the reason above the numbers -- the difference
between a broken link and a wrong one. The API has no such excuse and answers
422.

The parameters live in one dependency rather than in six handlers, so every
endpoint that reports on a window accepts the same two and reads the site's own
zone the same way. `from` is a Python keyword, hence the aliases.

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

## What a visit is, and what follows from it

Reading the established tools again — Plausible, Umami, Matomo — the gap this
time was not a dimension but a *shape*. All three report on the visit rather
than the pageview: where it started, where it ended, and whether it went
anywhere at all. This reported only the pageview.

A visit here is a **visitor-day**, and that is less a choice than a
consequence. The salt behind a visitor ID rotates at the site's own midnight,
so an identity cannot outlive the day it was minted in. There is no session to
hang these metrics on and there cannot be one without giving up the rotation,
which is the entire privacy argument.

It turns out to be the convenient unit rather than a limiting one, because it
is already the grain the aggregates are built on:

- **Bounce rate** — the share of visitors who read one page and went no
  further. Stored as a count of bounces on the daily total row, never as a
  rate, because rates do not add up: a day with four visits would otherwise
  weigh as heavily in a monthly figure as a day with forty thousand.
- **Entry pages** — the first page of each visit. The landing pages.
- **Exit pages** — the last page of each visit.

All three are additive across days for exactly the same reason unique visitors
are, and a visit cannot straddle midnight, so a day's numbers are complete on
their own. No session table, no thirty-minute inactivity timer, no sketch.
Entrances and exits needed no schema at all — they are new dimension values in
`daily_stats`, so they roll up, export to CSV and age out with everything else.

Two details worth pointing at:

- **The second column on an entry page is what the visit went on to read**, not
  a repeat of the first. Two landing pages can pull the same number of visits
  and be worth entirely different amounts, and visits alone cannot tell them
  apart.
- **The ranking breaks ties on the primary key, not just the timestamp.**
  Batched writes can land two of a visitor's pageviews in the same instant, and
  without a total order the first page of that visit is whichever row the
  database felt like returning — so the rollups and the raw queries would
  disagree at random, which is the one failure `tests/test_reports.py` exists to
  catch.

### Visit duration is missing on purpose

Every one of those tools reports it, and it is the obvious fourth metric. It is
absent because it cannot be computed honestly against this unit.

A session in Plausible or Matomo ends after half an hour of inactivity, so the
last timestamp minus the first is a real number. Here the unit runs to
midnight, so the same arithmetic would report the gap between somebody's
breakfast reading and their evening reading as one seven-hour visit — and
inflate the site average with it, invisibly, in a way no one looking at the
dashboard could detect. A number that is wrong where nobody can see it is worse
than a number that is missing.

Measuring it properly means a session identity that survives a gap, which means
a salt that does not rotate, which is the one thing this project will not
trade. The note is here so the gap reads as a decision rather than an oversight.

### Upgrading an existing install

Bounce counts are computed when a day is rolled up, and the routine refresh
only reaches back two days. Days aggregated before this change will read as a
0% bounce rate until they are rebuilt, which is one command:

```bash
python manage.py rollup --days 400
```

Entry and exit pages are in the same position, and the same rebuild fills them
in. Nothing needs re-ingesting: both are derived from raw events that are still
there, subject to whatever retention is set.

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
pages, entry and exit pages, sources, countries, devices, browsers and
operating systems.

Almost none of it needs JavaScript:

- **The chart is inline SVG** whose geometry — points, area path, gridlines and
  axis ticks — is computed in Python. No charting library, and no Node toolchain
  to build one.
- **The axis is cut into bands the ceiling divides by**, so every gridline is a
  whole number. Halving a ceiling of 125 drew a line at 62.5 and labelled it 62,
  which is a gridline sitting slightly off the value it claims; it now reads
  0/25/50/75/100/125. The labels live in a gutter beside the plot rather than
  over the top of the curve, and the x-axis carries up to seven ticks aligned to
  the buckets they name instead of a start and end date underneath.
- **The chrome is deliberately recessive.** Solid hairline gridlines rather than
  dashes, which read as a threshold when they are just a grid; no glow on the
  curve; a fill at a tenth of its old opacity. A saturated line over a heavy
  gradient on near-black is a game HUD, not an instrument.
- **The breakdown tabs are radio inputs and sibling selectors**, so switching
  between six dimensions works with scripting turned off.
- **Growth from zero shows no percentage.** A jump from nothing is not a
  percentage increase, and rendering it as one would be a lie the dashboard
  tells every time a site starts.
- **"Today" is compared against yesterday up to the same hour**, because
  measuring a half-finished day against a whole one shows a fall every morning.

The two scripts that do exist are small: one refreshes the live visitor count
and pauses while the tab is hidden, the other toggles the theme.

Dark is the default whatever the visitor's system is set to, and light is there
for anyone who asks for it with the toggle. The choice is applied inline in
`<head>` so there is no flash of the wrong theme on load.

The palette is burnt orange on warm neutrals, and it lives entirely in tokens:
dark on a bare `:root`, light on `[data-theme="light"]`. Following
`prefers-color-scheme` instead needed the dark palette written out twice, once
for the media query and once for the explicit choice; committing to a default
took a whole block out. Everything downstream follows
`--accent` without knowing what it is — the chart line and its gradient, the
sparklines, the breakdown bars, the focus ring and the brand mark.

One token exists only because contrast does not survive a palette swap.
`--on-accent` is the colour of text sitting *on* the accent, white in the light
theme and near-black in the dark one — that is the primary button on every
sign-in and sign-up form, and orange is exactly the hue where getting it wrong
is easiest: a vivid `#f97316` carries white at 2.8:1. The light accent is
therefore a burnt `#c2410c` rather than a brighter orange. Both themes clear
WCAG AA on every text pair, the tightest being 5.02:1, and the live dot clears
the 3:1 that applies to it as a graphic rather than as text. Static assets carry a hash of their contents in the URL, so a browser
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
| `GET /api/stats/{site}/breakdown/{prop}` | Top pages, entry pages, exit pages, sources, countries, devices, browsers, systems, screens, goals, campaigns, or mediums |
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

### Reading it without a browser

Until recently the stats API could only be reached by something holding a
session cookie, which meant it was an API in shape only -- nothing could be
scripted, embedded in a status page, or pulled into a warehouse. Plausible and
Umami both solve that with a key, and so does this:

```bash
curl -H "Authorization: Bearer $BEACON_KEY" \
  "https://your-host/api/stats/yoursite.com/summary?period=30d"
```

Keys are made and revoked on the account page, and shown exactly once.

**A key is strictly weaker than a session, by construction rather than by a
permission flag.** It resolves through `require_readable_site` and nowhere
else, and every route that changes something -- publishing a dashboard, moving
a site's timezone, adding a site, minting another key -- resolves through
`require_owned_site`, which only ever consults the session. So a leaked key
reads exactly what a published dashboard already exposes, and can do nothing.
A test posts a key at all four of those routes and asserts `401` from each.

**Hashed with SHA-256, not bcrypt**, which is the opposite of what
`app/services/passwords.py` does and is deliberate. bcrypt's cost factor exists
to make guessing a low-entropy human password expensive. A key here is 32 bytes
from `secrets`, so there is no dictionary to guess from and the cost buys
nothing -- while charging a hundred milliseconds on every API request, and
forcing a scan: bcrypt salts each hash separately, so finding a key would mean
loading every row and comparing them one at a time. A SHA-256 digest is found
by indexed equality in constant work.

**Use is recorded as a day, not a moment.** "Is this key still live?" is
answerable from a date, whereas a timestamp per call accumulates into a record
of when its owner works -- which is the sort of thing this project exists in
order not to keep. The write only happens when the day changes, so a busy key
does not write a row per request; a test watches the statements rather than the
value, because an accidental write on every request is invisible in the value.

Deliberately not rate limited, unlike Plausible's, whose limit is there because
it is a shared multi-tenant service. Every route a key reaches reads
pre-aggregated rows in about a millisecond, and on a self-hosted instance the
only account that can exhaust the host is the one that owns it.

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

Feature-complete and tested: 466 tests, 100% coverage of `app/`, clean under
`mypy --strict`, running on both SQLite and Postgres in CI.

Ideas worth doing next, roughly in order of how much they would add:

- **Funnels** — the one thing Plausible and Umami have that this does not, and
  the goals table already holds the events they would be built from. Ordering
  steps within a visit is the same window function the entry and exit pages
  already use.
- **Visit duration**, if the visit ever stops being the whole day. It cannot be
  measured honestly against the current unit; see above.
- **HyperLogLog sketches**, if cross-day unique visitors were ever wanted. They
  would need the salt rotation to go, which is the entire privacy argument, so
  this is a genuine product decision rather than an engineering one.
