# Running Beacon

What to do when it is yours to keep running. Written because there was
nothing: no backup procedure, no restore, no upgrade path, for a product whose
entire value is data it has kept.

Everything below assumes the Docker Compose stack. Adjust the container names
if you run it another way.

---

## Back it up

**This is the only section that cannot wait.** Beacon's whole proposition is
retained history, and once raw events age past
`BEACON_RAW_EVENT_RETENTION_DAYS` the aggregates are the *only* copy of them.
There is no reconstructing a lost month.

```bash
docker compose exec -T db pg_dump -U beacon --format=custom beacon > beacon-$(date +%F).dump
```

Keep it somewhere that is not the same machine. Test it (below) at least once,
because an untested backup is a belief rather than a backup.

On SQLite, copy the file *through SQLite* rather than with `cp` — a plain copy
of a live database can be torn:

```bash
sqlite3 beacon.db ".backup 'beacon-$(date +%F).db'"
```

### What is in the dump, and what that obliges you to

The database holds no IP addresses, no user-agent strings, and no query
strings — that is structural, and `tests/test_privacy.py` enforces it. It does
hold hashed passwords, API-key digests, session-secret-signed material, and
every site's traffic history. Treat a dump as you would treat the database.

### Restore, and proving the backup is real

```bash
docker compose stop app
docker compose exec -T db psql -U beacon -c 'DROP DATABASE beacon;' -d postgres
docker compose exec -T db psql -U beacon -c 'CREATE DATABASE beacon;' -d postgres
docker compose exec -T db pg_restore -U beacon -d beacon < beacon-2026-09-01.dump
docker compose start app
```

Rehearse it against a scratch database before you ever need it. A restore you
have not performed is a procedure you do not have.

That sentence was true of this page. The commands sat here unrun, which is the
same standing as a comment claiming what code does. The SQLite path is now
rehearsed on every push by `tests/test_backup_restore.py`: it fills a database,
backs it up with the command printed above, deletes the original, restores, and
counts the rows back. A backup that silently captured nothing fails it -- which
is checked by making the restore return an empty database and watching the test
notice.

The Postgres path above still needs a real server and is not covered. That is a
gap, and naming it is better than implying the whole page is exercised.

---

## Upgrade

Migrations run automatically on container start, so the ordinary path is:

```bash
docker compose pull    # or: docker compose build
docker compose up -d
```

**Back up first.** Alembic downgrades exist and are tested to `base`, but a
downgrade that drops a column drops the data in it. Rolling *back* an upgrade
is not free.

If you would rather run migrations yourself:

```bash
docker compose run --rm app alembic upgrade head
```

### What a password change does to everyone

Changing a password bumps that account's session epoch, and every cookie minted
before it stops being accepted. This is deliberate — a reset prompted by a
stolen session has to eject that session — but it means a password change signs
that user out everywhere, immediately.

---

## What runs on its own

One background loop, every `BEACON_ROLLUP_INTERVAL_SECONDS` (0 disables it,
which is the default — **set it, or nothing below happens**):

| Task | What it does |
|---|---|
| Rollup refresh | Rebuilds `daily_stats` / `hourly_stats` for days with new events |
| Salt purge | Deletes visitor salts older than two days |
| Throttle purge | Drops expired login-attempt records |
| Reset purge | Drops spent and expired password-reset links |
| Event purge | Deletes raw events past `BEACON_RAW_EVENT_RETENTION_DAYS` |

`BEACON_RAW_EVENT_RETENTION_DAYS=0` keeps raw events forever. That is the
default and is the safe choice until you have a reason otherwise.

### The one destructive interaction

Rebuilding a day's rollups reads raw events. If retention has already deleted
that day's events, a rebuild would replace real aggregates with zeroes — so it
refuses, and logs which days it skipped. If you see that warning, nothing is
wrong: it is the guard working. Do not "fix" it by widening the rebuild.

---

## Watch these

There is no metrics endpoint. What exists:

- **`GET /health`** — more than liveness. It does a database round trip and
  answers `503` with `{"status": "degraded", "database": "unreachable"}` if
  that fails, so a container whose database has gone away reports unhealthy
  instead of quietly serving errors. The Docker healthcheck already polls it.

  When the ingest buffer is enabled it also returns `queued_events` and
  `dropped_events`. **Alert on `dropped_events` moving.** A dropped event is
  the one failure this service survives in silence — nobody's browser
  complains, and the number is simply lower than it should be forever.
- **Logs** are one JSON object per line with `BEACON_LOG_JSON=true`, carrying a
  `request_id` that also comes back in the `X-Request-Id` header — so a user
  reporting a broken page can hand you the exact request.

Worth alerting on, if you have somewhere to send it:

| Signal | Why |
|---|---|
| `left N site-days alone` | Retention is eating days you can still rebuild |
| `dropped an event for <domain>` | A snippet is on a host nobody registered |
| `could not send mail` | Password resets are silently not arriving |
| 429s from nginx on `/api/event` | Either an attack, or your limit is too tight for a large NAT |

Logs record the *route template* (`/sites/{site_id}`), never the filled-in
path, so they contain no customer domains and no reset tokens. Do not "improve"
this by logging `request.url.path`.

---

## Things that will bite

**Country reporting is inert without a GeoIP database.** Beacon ships none —
it is a licensed file. Supply one and point `BEACON_GEOIP_DB_PATH` at it, or
accept that every visitor is `Unknown`.

**`BEACON_TRUST_PROXY_HEADERS` and the proxy are a pair.** With a proxy in
front and the setting off, every visitor collapses into one identity and one
failed login locks out everybody. With the setting on and the app reachable
around the proxy, visitors can forge their own identity. The compose file gets
this right; a hand-rolled deployment must too.

**The session secret must survive restarts.** Generate it once, keep it. A new
secret signs out every user on every deploy.

**Scale, honestly.** Measured at roughly 500 requests/second on one machine
against a few hundred thousand events. That is the tested envelope, not a
limit — but nobody has run this anywhere near a large site, and you would be
the first.
