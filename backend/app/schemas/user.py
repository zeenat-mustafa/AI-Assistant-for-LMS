from datetime import datetime
from pydantic import BaseModel
from app.models.user import UserRole


# ── Auth ──────────────────────────────────────────────────────────────────────

class Token(BaseModel):
    """Returned by POST /auth/login."""
    access_token: str
    token_type: str = "bearer"


class TokenData(BaseModel):
    """Payload decoded from a JWT."""
    user_id: int
    role: UserRole


# ── User ──────────────────────────────────────────────────────────────────────

class UserCreate(BaseModel):
    """Used internally by the seed function — not exposed as a public endpoint."""
    name: str
    email: str
    password: str
    role: UserRole


class UserRead(BaseModel):
    """Safe outward-facing representation of a User (no password)."""
    id: int
    name: str
    email: str
    role: UserRole
    created_at: datetime

    model_config = {"from_attributes": True}
