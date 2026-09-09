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

    # ── LLM: Gemini (two-tier, Gemini-only) ───────────────────────────────────
    # The provider chain is two Gemini models and nothing else. The primary
    # serves every call; on a quota/rate-limit error it falls back to the
    # secondary. There is no fast/pro split any more — one pair handles every
    # purpose. (Groq and Ollama were removed entirely; see llm_provider.py.)
    gemini_api_key: str = ""
    # Primary model — serves every call regardless of purpose.
    gemini_primary_model: str = "gemini-3.5-flash-lite"
    # Fallback model — used only when the primary hits a quota/rate-limit error.
    gemini_fallback_model: str = "gemini-3.1-flash-lite"

    # ── Demo seed users (MVP only — not for production) ───────────────────────
    demo_instructor_email: str = "instructor@demo.com"
    demo_instructor_password: str = "instructor123"
    demo_student_email: str = "student@demo.com"
    demo_student_password: str = "student123"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
