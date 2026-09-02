from pydantic_settings import BaseSettings
from pathlib import Path


class Settings(BaseSettings):
    # ── Database ──────────────────────────────────────────────────────────────
    database_url: str = "sqlite:///./lms.db"

    # ── File storage ──────────────────────────────────────────────────────────
    # Root directory for all uploaded files (assignments + submissions).
    # Resolved relative to the backend/ directory at runtime.
    storage_root: str = "storage/sessions"

    # ── Auth ──────────────────────────────────────────────────────────────────
    secret_key: str = "CHANGE_ME_IN_PRODUCTION_USE_A_LONG_RANDOM_STRING"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 480  # 8 hours — convenient for a demo

    # ── LLM: Gemini (primary) ─────────────────────────────────────────────────
    gemini_api_key: str = ""
    # Fast model for high-volume calls (rubric generation, evaluation, feedback).
    # gemini-flash-latest is Google's auto-updating alias for the newest Flash
    # release (currently resolves to gemini-3.8-flash, the newest stable GA Flash
    # as of 2026-09-02). Note: -latest may resolve to a preview release — pin to
    # a specific stable endpoint (e.g. gemini-3.6-flash) if you need guaranteed
    # stable behaviour.  Verify current models at https://ai.google.dev/models
    gemini_fast_model: str = "gemini-flash-latest"
    # Stronger model for low-volume reasoning tasks (ambiguous file matching).
    # There is currently no stable GA Gemini pro model (gemini-3.1-pro is still
    # Preview as of 2026-09-02).  gemini-3.8-flash is the most capable current
    # stable GA model and is used here instead.  Switch to gemini-pro-latest once
    # a stable pro model backs that alias.
    gemini_pro_model: str = "gemini-3.1-pro-preview"

    # ── LLM: Groq (fallback on Gemini quota/rate-limit errors) ───────────────
    groq_api_key: str = ""
    # Comparable fast open model on Groq (Llama 3.1 8B is currently available and fast).
    groq_fast_model: str = "llama-3.1-8b-instant"
    # Stronger Groq model for reasoning fallback.
    groq_pro_model: str = "llama-3.3-70b-versatile"

    # ── Demo seed users (MVP only — not for production) ───────────────────────
    demo_instructor_email: str = "instructor@demo.com"
    demo_instructor_password: str = "instructor123"
    demo_student_email: str = "student@demo.com"
    demo_student_password: str = "student123"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
