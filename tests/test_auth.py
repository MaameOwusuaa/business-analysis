import pytest

from insightforge.auth import AuthError, hash_password, login, sign_up, verify_password
from insightforge.db import init_db


def test_password_hashing_roundtrip():
    h = hash_password("s3cret-pass")
    assert h.startswith("pbkdf2_sha256$") and "s3cret" not in h
    assert verify_password("s3cret-pass", h)
    assert not verify_password("wrong-pass", h)


def test_signup_login_and_errors():
    init_db()
    u = sign_up("Acme Retail", "Retail & E-commerce", "Ama Mensah", "Ama@Acme.example", "goodpass1")
    assert login("ama@acme.example", "goodpass1").workspace_id == u.workspace_id
    with pytest.raises(AuthError):
        login("ama@acme.example", "badpass1")
    with pytest.raises(AuthError):
        sign_up("Acme 2", "Retail & E-commerce", "Ama", "ama@acme.example", "goodpass1")  # duplicate email
    with pytest.raises(AuthError):
        sign_up("Weak Co", "Other", "Kofi", "kofi@weak.example", "short")  # weak password
    with pytest.raises(AuthError):
        sign_up("Invalid Industry Co", "Unknown Industry", "Kofi", "kofi@industry.example", "goodpass123")
