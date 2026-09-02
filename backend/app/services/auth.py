"""
Auth utilities: password hashing, JWT creation/verification, current-user
dependency, and demo-user seeding.

Uses the `bcrypt` library directly (avoids passlib's Python 3.13 compatibility
issues with the newer bcrypt backend).
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Annotated

import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.user import User, UserRole
from app.schemas.user import TokenData

logger = logging.getLogger(__name__)

# ── Password hashing ──────────────────────────────────────────────────────────

def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


# ── JWT ───────────────────────────────────────────────────────────────────────

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


def create_access_token(user_id: int, role: UserRole) -> str:
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=settings.access_token_expire_minutes
    )
    payload = {
        "sub": str(user_id),
        "role": role.value,
        "exp": expire,
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


def decode_token(token: str) -> TokenData:
    credentials_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(
            token, settings.secret_key, algorithms=[settings.algorithm]
        )
        user_id: str | None = payload.get("sub")
        role_str: str | None = payload.get("role")
        if user_id is None or role_str is None:
            raise credentials_exc
        return TokenData(user_id=int(user_id), role=UserRole(role_str))
    except JWTError:
        raise credentials_exc


# ── FastAPI dependencies ──────────────────────────────────────────────────────

def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    token_data = decode_token(token)
    user = db.get(User, token_data.user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found.",
        )
    return user


def require_instructor(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    if current_user.role != UserRole.instructor:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Instructor access required.",
        )
    return current_user


def require_student(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    if current_user.role != UserRole.student:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Student access required.",
        )
    return current_user


# ── Demo user seeding ─────────────────────────────────────────────────────────

def seed_demo_users(db: Session) -> None:
    """
    Idempotently create the two demo users on first startup.
    Called from main.py lifespan — safe to call multiple times.
    """
    _ensure_user(
        db,
        name="Demo Instructor",
        email=settings.demo_instructor_email,
        password=settings.demo_instructor_password,
        role=UserRole.instructor,
    )
    _ensure_user(
        db,
        name="Demo Student",
        email=settings.demo_student_email,
        password=settings.demo_student_password,
        role=UserRole.student,
    )
    db.commit()
    logger.info("Demo users seeded.")


def _ensure_user(
    db: Session, *, name: str, email: str, password: str, role: UserRole
) -> User:
    existing = db.query(User).filter(User.email == email).first()
    if existing:
        return existing
    user = User(
        name=name,
        email=email,
        hashed_password=hash_password(password),
        role=role,
    )
    db.add(user)
    logger.info("Created demo user: %s (%s)", email, role.value)
    return user
