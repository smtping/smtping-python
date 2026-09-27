"""Official SMTPing SDK for Python."""

from ._client import AsyncSmtping, Smtping, band, is_email
from ._errors import (
    AuthenticationError,
    InsufficientCreditsError,
    JobFailedError,
    RateLimitError,
    SmtpingError,
    TimeoutError,
    ValidationError,
)

__version__ = "1.0.0"

__all__ = [
    "Smtping",
    "AsyncSmtping",
    "band",
    "is_email",
    "SmtpingError",
    "AuthenticationError",
    "InsufficientCreditsError",
    "RateLimitError",
    "ValidationError",
    "JobFailedError",
    "TimeoutError",
    "__version__",
]
