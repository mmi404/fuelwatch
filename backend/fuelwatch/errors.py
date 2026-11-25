"""Errors raised by the simulator client.

The simulator uses two different error shapes:

* validation and business rules: ``409 {"detail": {"code": ..., "message": ...}}``
* injected faults:               ``503 {"error": {"code": "FAULT_INJECTED", ...}}``
  (and, for a rejected stream connection, ``503 {"detail": {"code": ...}}``)

Both are normalised here. ``transient`` tells the retry layer whether trying
again can help: it never can for a 404, 409 or 422.
"""

from __future__ import annotations


class SimulatorError(Exception):
    """Base class. ``transient`` marks errors worth retrying."""

    transient: bool = False

    def __init__(self, message: str, *, status: int | None = None, code: str | None = None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code

    def __str__(self) -> str:
        parts = [self.message]
        if self.status is not None:
            parts.append(f"status={self.status}")
        if self.code:
            parts.append(f"code={self.code}")
        return " ".join(parts)


class SimulatorUnavailable(SimulatorError):
    """Network failure, timeout, or a 5xx (including injected faults). Retryable."""

    transient = True


class CircuitOpenError(SimulatorUnavailable):
    """The circuit breaker refused the call; nothing was sent. Counts as unavailable."""


class SimulatorRejected(SimulatorError):
    """A 409 business-rule rejection, for example ROUTE_CAPACITY_EXCEEDED."""


class SimulatorInvalidRequest(SimulatorError):
    """A 422: our request did not match the schema. A bug on our side."""


class SimulatorNotFound(SimulatorError):
    """A 404."""


class MalformedResponse(SimulatorError):
    """The simulator answered 2xx but the body did not match our models."""
