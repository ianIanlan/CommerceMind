import pytest

from core.auth import AuthenticationError, issue_token, verify_token


def test_signed_identity_token_round_trip():
    token = issue_token("demo-user", "test-secret", expires_in=60, now=1000)
    assert verify_token(token, "test-secret", now=1020)["sub"] == "demo-user"


def test_tampered_or_expired_token_is_rejected():
    token = issue_token("demo-user", "test-secret", expires_in=10, now=1000)
    with pytest.raises(AuthenticationError):
        verify_token(token + "x", "test-secret", now=1001)
    with pytest.raises(AuthenticationError):
        verify_token(token, "test-secret", now=1010)
