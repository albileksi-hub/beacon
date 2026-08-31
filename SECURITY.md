# Reporting a security issue

Please report vulnerabilities privately through GitHub's
[Report a vulnerability](https://github.com/albileksi-hub/beacon/security/advisories/new)
form rather than opening a public issue.

Beacon holds other people's visitor data, so a report is worth making even if
you are not certain: an unclear report is easier to deal with than a surprise.

I will acknowledge within 72 hours and tell you what I intend to do. If a fix
is warranted, the advisory and the commit will credit you unless you would
rather they did not.

## What is in scope

Anything reachable on a running instance, including:

- the collector at `POST /api/event`, which is open to the whole internet
- session handling, password reset, and the API tokens
- anything that lets one account see or change another account's data
- anything that lets a visitor influence what an operator sees or downloads

## What is known and deliberate

These are documented decisions rather than oversights, and are argued at length
in [docs/DESIGN.md](docs/DESIGN.md). A report that one of them is a bug will be
closed with a pointer; a report that the reasoning is *wrong* is welcome.

- **The site ID is public and cannot authorise anything.** It ships in the
  snippet on every page. The collector requires the reported URL to be on the
  registered domain, which stops copied snippets and ID-scraping spam, but it
  cannot stop a caller who fills the URL in correctly. No public collector can:
  it cannot hold a secret the browser does not also hand to every visitor.
- **`/api/event` is not rate limited by the application.** The shipped nginx
  configuration does it instead, for reasons set out in DESIGN.md. An instance
  run without a proxy in front has no limit, and that is the operator's call.
- **There is no cross-day visitor identity, by construction.** The salt rotates
  at the site's local midnight and is deleted after two days.

## Supported versions

The `main` branch. There are no released versions yet, and no backports.
