import pytest

from app.services.passwords import (
    MIN_PASSWORD_LENGTH,
    InvalidPassword,
    hash_password,
    verify_password,
)

GOOD_PASSWORD = "a-perfectly-fine-password"


def test_a_hash_verifies_against_its_password():
    assert verify_password(GOOD_PASSWORD, hash_password(GOOD_PASSWORD))


def test_a_wrong_password_does_not_verify():
    assert not verify_password("something else entirely", hash_password(GOOD_PASSWORD))


def test_the_same_password_hashes_differently_every_time():
    """Per-password salts, so identical passwords are not identifiable as such."""
    assert hash_password(GOOD_PASSWORD) != hash_password(GOOD_PASSWORD)


def test_short_passwords_are_rejected():
    with pytest.raises(InvalidPassword, match=str(MIN_PASSWORD_LENGTH)):
        hash_password("short")


def test_passwords_past_the_bcrypt_limit_are_rejected_not_truncated():
    """Truncating at 72 bytes would make a long passphrase weaker than it looks."""
    with pytest.raises(InvalidPassword, match="too long"):
        hash_password("x" * 73)


def test_a_malformed_hash_is_a_failed_login_not_a_crash():
    assert not verify_password(GOOD_PASSWORD, "not-a-bcrypt-hash")
