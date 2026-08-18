"""Password hashing.

bcrypt with a per-password salt and a deliberately slow work factor, so a
stolen database is expensive to attack offline.
"""

import bcrypt

MIN_PASSWORD_LENGTH = 8
# bcrypt ignores everything past 72 bytes. Silently truncating would make a
# long passphrase weaker than the user believes it to be, so it is rejected.
MAX_PASSWORD_BYTES = 72


class InvalidPassword(ValueError):
    pass


def validate(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise InvalidPassword(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    if len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        raise InvalidPassword("Password is too long.")


def hash_password(password: str) -> str:
    validate(password)
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii"))
    except ValueError:
        # A malformed or truncated hash is a failed login, not a crash.
        return False
