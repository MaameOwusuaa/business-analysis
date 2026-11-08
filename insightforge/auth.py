"""Business onboarding: workspace sign-up, login and audit logging."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
from dataclasses import dataclass

from sqlalchemy import select

from .db import get_engine
from .logging_setup import get_logger
from .schema import audit_log, users, workspaces

log = get_logger("auth")

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PBKDF2_ROUNDS = 240_000

INDUSTRIES = [
    "Any industry",
    "Agriculture",
    "Arts & Entertainment",
    "Construction",
    "Education",
    "Energy & Utilities",
    "Financial Services",
    "Healthcare & Life Sciences",
    "Hospitality & Tourism",
    "Insurance",
    "Legal & Professional Services",
    "Logistics & Transportation",
    "Manufacturing",
    "Nonprofit & Public Sector",
    "Real Estate",
    "Retail & E-commerce",
    "Software & Technology",
    "Telecommunications",
    "Other",
]


class AuthError(ValueError):
    """Raised for user-correctable sign-up / login problems."""


@dataclass
class SessionUser:
    user_id: int
    workspace_id: int
    email: str
    full_name: str
    role: str
    workspace_name: str


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ROUNDS)
    return "pbkdf2_sha256${}${}${}".format(
        _PBKDF2_ROUNDS, base64.b64encode(salt).decode(), base64.b64encode(digest).decode()
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, rounds, salt_b64, digest_b64 = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), base64.b64decode(salt_b64), int(rounds)
        )
        return hmac.compare_digest(digest, base64.b64decode(digest_b64))
    except (ValueError, TypeError):
        return False


def _validate_signup(business_name: str, full_name: str, email: str, password: str, industry: str) -> None:
    if len(business_name.strip()) < 2:
        raise AuthError("Business name is required.")
    if industry not in INDUSTRIES:
        raise AuthError("Choose a listed industry.")
    if len(full_name.strip()) < 2:
        raise AuthError("Your name is required.")
    if not _EMAIL_RE.match(email.strip()):
        raise AuthError("Enter a valid email address.")
    if len(password) < 8 or password.isalpha() or password.isdigit():
        raise AuthError("Password must be at least 8 characters and mix letters with numbers or symbols.")


def audit(action: str, workspace_id: int | None = None, user_id: int | None = None, **detail) -> None:
    try:
        with get_engine().begin() as conn:
            conn.execute(
                audit_log.insert().values(
                    workspace_id=workspace_id,
                    user_id=user_id,
                    action=action,
                    detail=json.dumps(detail, default=str)[:4000],
                )
            )
    except Exception:  # auditing must never break the user flow
        log.exception("audit write failed action=%s", action)


def sign_up(
    business_name: str,
    industry: str,
    full_name: str,
    email: str,
    password: str,
    objective: str = "",
    country: str = "Ghana",
) -> SessionUser:
    _validate_signup(business_name, full_name, email, password, industry)
    email = email.strip().lower()
    engine = get_engine()
    with engine.begin() as conn:
        exists = conn.execute(select(users.c.id).where(users.c.email == email)).first()
        if exists:
            raise AuthError("An account with this email already exists. Please log in.")
        ws_id = conn.execute(
            workspaces.insert().values(
                name=business_name.strip(),
                industry=industry,
                country=country,
                objective=objective.strip() or None,
            )
        ).inserted_primary_key[0]
        user_id = conn.execute(
            users.insert().values(
                workspace_id=ws_id,
                email=email,
                full_name=full_name.strip(),
                password_hash=hash_password(password),
                role="owner",
            )
        ).inserted_primary_key[0]
    audit("workspace.created", ws_id, user_id, business=business_name.strip(), industry=industry)
    log.info("workspace created workspace_id=%s", ws_id)
    return SessionUser(user_id, ws_id, email, full_name.strip(), "owner", business_name.strip())


def login(email: str, password: str) -> SessionUser:
    email = email.strip().lower()
    with get_engine().connect() as conn:
        row = conn.execute(
            select(users, workspaces.c.name.label("workspace_name"))
            .join(workspaces, workspaces.c.id == users.c.workspace_id)
            .where(users.c.email == email)
        ).mappings().first()
    if not row or not verify_password(password, row["password_hash"]):
        audit("auth.login_failed", None, None, email_domain=email.split("@")[-1])
        raise AuthError("Incorrect email or password.")
    audit("auth.login", row["workspace_id"], row["id"])
    return SessionUser(
        row["id"], row["workspace_id"], row["email"], row["full_name"], row["role"], row["workspace_name"]
    )


def get_workspace(workspace_id: int) -> dict:
    with get_engine().connect() as conn:
        row = conn.execute(select(workspaces).where(workspaces.c.id == workspace_id)).mappings().first()
    return dict(row) if row else {}


def update_workspace(workspace_id: int, **values) -> None:
    allowed = {k: v for k, v in values.items() if k in {"name", "country", "objective", "active_use_case", "industry"}}
    if not allowed:
        return
    with get_engine().begin() as conn:
        conn.execute(workspaces.update().where(workspaces.c.id == workspace_id).values(**allowed))
