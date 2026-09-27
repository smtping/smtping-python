from typing import Any, Optional


class SmtpingError(Exception):
    """Base error. `status` is the HTTP code (0 for client-side errors), `body` the parsed response."""

    def __init__(self, message: str, status: int = 0, body: Any = None) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.body = body


class AuthenticationError(SmtpingError):
    pass


class InsufficientCreditsError(SmtpingError):
    pass


class RateLimitError(SmtpingError):
    pass


class ValidationError(SmtpingError):
    pass


class TimeoutError(SmtpingError):  # noqa: A001
    pass


class JobFailedError(SmtpingError):
    def __init__(self, message: str, job: Optional[dict] = None) -> None:
        super().__init__(message, 0, job)
        self.job = job


def error_for(status: int, body: Any) -> SmtpingError:
    msg = None
    if isinstance(body, dict):
        msg = body.get("error") or body.get("message")
    msg = msg or f"SMTPing API returned HTTP {status}"
    if status in (401, 403):
        return AuthenticationError(msg, status, body)
    if status == 402:
        return InsufficientCreditsError(msg, status, body)
    if status == 429:
        return RateLimitError(msg, status, body)
    if status in (400, 422):
        return ValidationError(msg, status, body)
    return SmtpingError(msg, status, body)
