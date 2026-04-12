"""Exceptions raised by the HaaS client."""


class HaasError(Exception):
    """Base exception for all HaaS API errors."""

    def __init__(self, message: str, status_code: int = 0):
        super().__init__(message)
        self.status_code = status_code


class NotFoundError(HaasError):
    """Raised when the requested resource does not exist (HTTP 404)."""


class AuthenticationError(HaasError):
    """Raised when the API key is invalid or missing (HTTP 401)."""


class ForbiddenError(HaasError):
    """Raised when the action is not allowed (HTTP 403)."""


class ServerError(HaasError):
    """Raised on unexpected server errors (HTTP 5xx)."""
