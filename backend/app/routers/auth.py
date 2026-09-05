"""
POST /auth/login  — exchange email + password for a JWT.
POST /auth/me     — return the currently authenticated user (useful for the
                    frontend to fetch role info on page load).
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User, UserRole
from app.schemas.user import Token, UserRead, UserRegister
from app.services.auth import (
    create_access_token,
    get_current_user,
    hash_password,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=Token, summary="Obtain a JWT access token")
def login(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: Annotated[Session, Depends(get_db)],
) -> Token:
    """
    Accepts standard OAuth2 form fields (username = email, password).
    Returns a Bearer JWT valid for `access_token_expire_minutes`.
    """
    user: User | None = db.query(User).filter(User.email == form_data.username).first()
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = create_access_token(user_id=user.id, role=user.role)
    return Token(access_token=token)


@router.get("/me", response_model=UserRead, summary="Return the current user")
def me(current_user: Annotated[User, Depends(get_current_user)]) -> UserRead:
    return UserRead.model_validate(current_user)


@router.post(
    "/register",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new student account",
)
def register(
    body: UserRegister,
    db: Annotated[Session, Depends(get_db)],
) -> UserRead:
    """
    Public endpoint — no auth required.
    Always creates a ``student`` account; role cannot be set by the caller.
    Returns 409 if the email is already taken.
    """
    existing = db.query(User).filter(User.email == body.email).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"An account with email '{body.email}' already exists.",
        )
    user = User(
        name=body.name,
        email=body.email,
        hashed_password=hash_password(body.password),
        role=UserRole.student,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return UserRead.model_validate(user)
