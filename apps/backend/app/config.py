"""Environment configuration; no credentials are loaded from the repository."""

import os
from dataclasses import dataclass, field


def truthy(value: str) -> bool:
    return value.lower() in {"1", "true", "yes"}


@dataclass
class Settings:
    devmode: bool = field(default_factory=lambda: truthy(os.getenv("DEVMODE", "false")))
    mongodb_uri: str = field(default_factory=lambda: os.getenv("MONGODB_URI", "mongodb://localhost:27017"))
    mongodb_database: str = field(default_factory=lambda: os.getenv("MONGODB_DATABASE", "campus_exchange"))
    cookie_secure: bool = field(default_factory=lambda: truthy(os.getenv("COOKIE_SECURE", "false")))
    allowed_origins: tuple[str, ...] = field(default_factory=lambda: tuple(x.strip().rstrip("/") for x in os.getenv("ALLOWED_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173,http://localhost:8080,http://127.0.0.1:8080").split(",") if x.strip()))
    student_check_limit_per_hour: int = field(default_factory=lambda: int(os.getenv("STUDENT_CHECK_LIMIT_PER_HOUR", "30")))
    session_days: int = field(default_factory=lambda: int(os.getenv("PROFILE_DAYS", "7")))

    def __post_init__(self) -> None:
        for name in ("student_check_limit_per_hour", "session_days"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
