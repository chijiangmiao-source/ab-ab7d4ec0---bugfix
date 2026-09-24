"""Stable, locatable error types.

Every error carries a stable machine-readable ``code``. Input-shape errors
additionally carry a JSON Pointer (RFC 6901) style ``pointer`` locating the
offending request element, so callers never have to parse free text.
"""

from __future__ import annotations

from typing import Any


class ValidationError(Exception):
    """The request body is not a well-formed problem (HTTP 400)."""

    def __init__(self, code: str, message: str, pointer: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.pointer = pointer

    def to_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.pointer:
            body["pointer"] = self.pointer
        return body


class TopologyError(Exception):
    """The input is well formed but the required endpoints cannot be met.

    Covers dangling (isolated) endpoints and endpoints split across connected
    components (HTTP 422). Never carries a partial subnet.
    """

    def __init__(
        self,
        code: str,
        message: str,
        pointer: str = "",
        components: list[list[str]] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.pointer = pointer
        self.components = components or []

    def to_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.pointer:
            body["pointer"] = self.pointer
        if self.components:
            body["components"] = self.components
        return body
