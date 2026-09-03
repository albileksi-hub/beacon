"""Accounts, sites, and the ownership checks that keep tenants apart."""

import datetime as dt
import re
import threading

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Membership, Role, Site, User
from app.services import zones
from app.services.passwords import hash_password, verify_password

SESSION_KEY = "user_id"
# The account's session_epoch at the moment the cookie was minted. Compared
# on every request, so changing a password ejects older cookies.
EPOCH_KEY = "epoch"

# A real hash of a password nobody holds. Verifying against it makes a login
# for an unknown address cost the same as one for a known address, so the
# endpoint cannot be used to discover which emails are registered.
_DECOY_HASH = "$2b$12$Q1LLlYCR3KZpwml.5ELrE.pM7WHVKreWXti.uXcDy4qx1QkwpQkBm"


class EmailAlreadyRegistered(ValueError):
    pass


class DomainAlreadyRegistered(ValueError):
    pass


class InvalidDomain(ValueError):
    pass


# The sites.domain column, and the longest a hostname can be.
MAX_DOMAIN_LENGTH = 253


def normalise_email(email: str) -> str:
    return email.strip().lower()


def normalise_domain(domain: str) -> str:
    """Reduce whatever the user typed to a bare hostname."""
    cleaned = domain.strip().lower()
    for prefix in ("https://", "http://"):
        cleaned = cleaned.removeprefix(prefix)
    cleaned = cleaned.split("/")[0].removeprefix("www.")
    return cleaned


def register(db: Session, *, email: str, password: str) -> User:
    address = normalise_email(email)
    if db.scalar(select(User).where(User.email == address)) is not None:
        raise EmailAlreadyRegistered("That email address is already registered.")

    user = User(email=address, password_hash=hash_password(password))
    db.add(user)
    db.commit()
    return user


def authenticate(db: Session, *, email: str, password: str) -> User | None:
    user = db.scalar(select(User).where(User.email == normalise_email(email)))

    if user is None:
        # Spend the same time as a real check would; see _DECOY_HASH.
        verify_password(password, _DECOY_HASH)
        return None

    return user if verify_password(password, user.password_hash) else None


def set_password(*, user: User, password: str) -> None:
    """Replace the password and invalidate every session opened under the old one.

    The epoch bump is the half that is easy to leave out: without it a reset
    prompted by a stolen session leaves that session working, which is exactly
    the situation the reset was for.

    Takes no session and does not commit, unlike its neighbours here.
    Changing a password and spending the reset links that authorised it have
    to land in one transaction, so that boundary belongs to the caller doing
    both.
    """
    user.password_hash = hash_password(password)
    user.session_epoch += 1


# A hostname label: letters, digits and hyphens, not leading or trailing with
# one, at most 63 characters. Anything else is not a domain, whatever else it
# might be.
_LABEL = r"(?!-)[a-z0-9-]{1,63}(?<!-)"
_HOSTNAME = re.compile(rf"^{_LABEL}(\.{_LABEL})*$")


def as_hostname(cleaned: str) -> str:
    """Whatever the user typed, reduced to a hostname or refused.

    Deliberately not part of normalise_domain: the collector calls that on
    every single event, and this is a registration-time question asked once.

    Nothing checked the shape before, so `quote".example` could be registered.
    That domain is interpolated into the CSV export's Content-Disposition, and
    the quote closed the filename early:

        attachment; filename="beacon-quote".example-...csv"

    which lets a domain decide where the filename ends -- and with
    `a".exe;x="b.example`, what it appears to be called. CRLF went in too;
    Starlette percent-encodes a Location header so nothing split there, but
    relying on that is relying on somebody else's escaping to cover a value
    this service never should have stored.

    Non-ASCII is encoded to punycode rather than refused, so a real
    international domain works: munchen.de with an umlaut arrives as
    xn--mnchen-3ya.de, which is the form that appears in DNS anyway.
    """
    if not cleaned.isascii():
        try:
            cleaned = cleaned.encode("idna").decode("ascii")
        except UnicodeError as error:
            raise InvalidDomain("That does not look like a domain name.") from error

    if not _HOSTNAME.match(cleaned):
        raise InvalidDomain("That does not look like a domain name.")

    return cleaned


def add_site(db: Session, *, owner: User, domain: str) -> Site:
    hostname = normalise_domain(domain)
    if not hostname:
        raise InvalidDomain("Enter a domain.")
    if len(hostname) > MAX_DOMAIN_LENGTH:
        # SQLite ignores VARCHAR lengths, so without this an over-long domain
        # is accepted in development and rejected in production.
        raise InvalidDomain("That domain is too long.")
    hostname = as_hostname(hostname)
    if db.scalar(select(Site).where(Site.domain == hostname)) is not None:
        raise DomainAlreadyRegistered("That domain is already being tracked.")

    site = Site(domain=hostname, owner_id=owner.id, timezone=zones.DEFAULT)
    db.add(site)
    db.flush()
    # The owner is a member like everyone else, so every access check asks one
    # question of one table rather than special-casing whoever created it.
    db.add(Membership(site_id=site.id, user_id=owner.id, role=Role.OWNER))
    db.commit()
    # This worker starts collecting immediately rather than at the end of the
    # interval; other workers still wait for theirs to lapse.
    forget_registered_domains()
    return site


def sites_for(db: Session, owner: User) -> list[Site]:
    """Every site this person can open, whatever their role on it."""
    return list(
        db.scalars(
            select(Site)
            .join(Membership, Membership.site_id == Site.id)
            .where(Membership.user_id == owner.id)
            .order_by(Site.domain)
        )
    )


def role_for(db: Session, *, user: User | None, site: Site) -> Role | None:
    """What this person may do with this site, or None if they may not."""
    if user is None:
        return None

    granted = db.scalar(
        select(Membership.role).where(
            Membership.site_id == site.id, Membership.user_id == user.id
        )
    )
    return Role(granted) if granted is not None else None


def _site_for(db: Session, *, user: User, domain: str, allowed: set[Role]) -> Site | None:
    """The site, only if this user holds one of these roles on it.

    Callers turn a None into a 404 rather than a 403: a 403 would confirm that
    the domain exists on the platform, which lets anyone enumerate customers.
    """
    site = db.scalar(select(Site).where(Site.domain == normalise_domain(domain)))
    if site is None:
        return None

    return site if role_for(db, user=user, site=site) in allowed else None


def owned_site(db: Session, *, owner: User, domain: str) -> Site | None:
    """The site, only for its owner: deleting it, and deciding who else sees it."""
    return _site_for(db, user=owner, domain=domain, allowed={Role.OWNER})


def member_site(db: Session, *, user: User, domain: str) -> Site | None:
    """The site, for anyone actually invited to it, whatever their role.

    Distinct from readable_site, which also says yes to a stranger when the
    owner has published the dashboard. Publishing is a decision about the
    numbers on that page; it is not a decision to hand over the whole export.
    """
    return _site_for(db, user=user, domain=domain, allowed=set(Role))


def administered_site(db: Session, *, user: User, domain: str) -> Site | None:
    """The site, for anyone who may change its settings.

    Publishing and the timezone are administration rather than ownership: an
    admin does the work, the owner decides who is an admin.
    """
    return _site_for(db, user=user, domain=domain, allowed={Role.OWNER, Role.ADMIN})


# The collector checks a domain against this on every single event, and it
# measured 30% of the cost of ingesting one. The set of registered domains is
# small and changes rarely, so it is read once per interval rather than once per
# event. The window is short because another worker may have added a site, and
# only time can tell this process about that.
REGISTRY_TTL = dt.timedelta(seconds=30)

_registry_lock = threading.Lock()
_registry: tuple[dt.datetime, dict[str, str]] | None = None


def forget_registered_domains() -> None:
    global _registry
    with _registry_lock:
        _registry = None


def tracked_sites(db: Session, *, now: dt.datetime | None = None) -> dict[str, str]:
    """Every domain the collector accepts, mapped to the zone it reckons days in.

    The zone travels with the domain because the collector needs it on every
    event, to work out which of that site's days the event belongs to.
    """
    global _registry
    moment = now or dt.datetime.now(dt.UTC)

    with _registry_lock:
        cached = _registry
    if cached is not None and moment < cached[0]:
        return cached[1]

    sites = {
        domain: timezone
        for domain, timezone in db.execute(select(Site.domain, Site.timezone))
    }
    with _registry_lock:
        _registry = (moment + REGISTRY_TTL, sites)
    return sites


def site_is_registered(db: Session, domain: str) -> bool:
    return domain in tracked_sites(db)


def timezone_for(db: Session, domain: str) -> str:
    """The site's zone, or UTC for a domain that is not tracked here."""
    return tracked_sites(db).get(domain, zones.DEFAULT)


def set_timezone(db: Session, *, site: Site, timezone: str) -> Site:
    site.timezone = zones.validate(timezone)
    db.commit()
    # The collector reads the zone on every event; it should not keep using the
    # old one until the interval lapses.
    forget_registered_domains()
    return site


def readable_site(db: Session, *, viewer: User | None, domain: str) -> Site | None:
    """The site, if this viewer is allowed to see its numbers.

    Any role at all is enough to read, or the owner has published it. Anything
    else is a 404 to the caller, for the reason in _site_for.
    """
    hostname = normalise_domain(domain)
    site = db.scalar(select(Site).where(Site.domain == hostname))
    if site is None:
        return None
    if site.public:
        return site

    return site if role_for(db, user=viewer, site=site) is not None else None


class MembershipError(ValueError):
    """Anything that would leave the members of a site in a nonsense state."""


def members_of(db: Session, site: Site) -> list[tuple[User, Role]]:
    """Everyone with access, owner first and then by email."""
    rows = db.execute(
        select(User, Membership.role)
        .join(Membership, Membership.user_id == User.id)
        .where(Membership.site_id == site.id)
        .order_by(Membership.role != Role.OWNER, User.email)
    )
    return [(user, Role(role)) for user, role in rows]


def add_member(db: Session, *, site: Site, email: str, role: Role) -> Membership:
    """Grant someone access to a site.

    They must already have an account. Inviting an address that has never
    signed up would mean issuing a token and sending mail, and there is no mail
    in this project yet -- so this refuses plainly rather than silently doing
    nothing, which is the failure a half-built invite flow actually produces.
    """
    if role is Role.OWNER:
        raise MembershipError("A site has one owner. Transfer it instead of adding another.")

    address = normalise_email(email)
    if not address:
        raise MembershipError("Enter an email address.")

    user = db.scalar(select(User).where(User.email == address))
    if user is None:
        raise MembershipError(f"No account for {address}. They need to sign up first.")

    if role_for(db, user=user, site=site) is not None:
        raise MembershipError(f"{address} already has access to this site.")

    membership = Membership(site_id=site.id, user_id=user.id, role=role)
    db.add(membership)
    db.commit()
    return membership


def _members_role(db: Session, *, site: Site, user_id: int) -> Membership | None:
    return db.scalar(
        select(Membership).where(
            Membership.site_id == site.id, Membership.user_id == user_id
        )
    )


def set_member_role(db: Session, *, site: Site, user_id: int, role: Role) -> Membership:
    """Change what someone may do. The owner's own row is not up for editing."""
    if role is Role.OWNER:
        raise MembershipError("A site has one owner. Transfer it instead.")

    membership = _members_role(db, site=site, user_id=user_id)
    if membership is None:
        raise MembershipError("That person does not have access to this site.")
    if membership.role == Role.OWNER:
        raise MembershipError("The owner's role cannot be changed.")

    membership.role = role
    db.commit()
    return membership


def remove_member(db: Session, *, site: Site, user_id: int) -> None:
    """Take access away.

    The owner cannot be removed, including by themselves: a site with no owner
    is one nobody can publish, rename or delete, and nothing else in the system
    would notice it had happened.
    """
    membership = _members_role(db, site=site, user_id=user_id)
    if membership is None:
        raise MembershipError("That person does not have access to this site.")
    if membership.role == Role.OWNER:
        raise MembershipError("The owner cannot be removed. Transfer the site instead.")

    db.delete(membership)
    db.commit()


def set_visibility(db: Session, *, site: Site, public: bool) -> Site:
    site.public = public
    db.commit()
    return site
