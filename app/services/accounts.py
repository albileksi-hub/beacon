"""Accounts, sites, and the ownership checks that keep tenants apart."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Site, User
from app.services.passwords import hash_password, verify_password

SESSION_KEY = "user_id"

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


def add_site(db: Session, *, owner: User, domain: str) -> Site:
    hostname = normalise_domain(domain)
    if not hostname:
        raise InvalidDomain("Enter a domain.")
    if db.scalar(select(Site).where(Site.domain == hostname)) is not None:
        raise DomainAlreadyRegistered("That domain is already being tracked.")

    site = Site(domain=hostname, owner_id=owner.id)
    db.add(site)
    db.commit()
    return site


def sites_for(db: Session, owner: User) -> list[Site]:
    return list(
        db.scalars(select(Site).where(Site.owner_id == owner.id).order_by(Site.domain))
    )


def owned_site(db: Session, *, owner: User, domain: str) -> Site | None:
    """The site, only if this user owns it.

    Callers turn a None into a 404 rather than a 403: a 403 would confirm that
    the domain exists on the platform, which lets anyone enumerate customers.
    """
    return db.scalar(
        select(Site).where(Site.domain == normalise_domain(domain), Site.owner_id == owner.id)
    )


def site_is_registered(db: Session, domain: str) -> bool:
    return db.scalar(select(Site.id).where(Site.domain == domain)) is not None
