"""Multi-pass forensic investigation orchestrator using the Claude Agent SDK."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ekchain.orchestrator.errors import AuthenticationError, ModelNotAvailableError
    from ekchain.orchestrator.evidence import EvidenceContext, ServerBridge
    from ekchain.orchestrator.log_tailer import LogTailer
    from ekchain.orchestrator.roles import RoleRunner
    from ekchain.orchestrator.runner import Orchestrator
    from ekchain.orchestrator.session import SessionExecutor

__all__ = [
    "AuthenticationError",
    "EvidenceContext",
    "LogTailer",
    "ModelNotAvailableError",
    "Orchestrator",
    "RoleRunner",
    "ServerBridge",
    "SessionExecutor",
]


def __getattr__(name: str) -> object:
    """Lazy-load public symbols to avoid import-order issues with SDK stubs."""
    if name in ("AuthenticationError", "ModelNotAvailableError"):
        from ekchain.orchestrator.errors import (
            AuthenticationError,
            ModelNotAvailableError,
        )

        return AuthenticationError if name == "AuthenticationError" else ModelNotAvailableError
    if name == "Orchestrator":
        from ekchain.orchestrator.runner import Orchestrator

        return Orchestrator
    if name == "SessionExecutor":
        from ekchain.orchestrator.session import SessionExecutor

        return SessionExecutor
    if name == "RoleRunner":
        from ekchain.orchestrator.roles import RoleRunner

        return RoleRunner
    if name in ("EvidenceContext", "ServerBridge"):
        from ekchain.orchestrator.evidence import EvidenceContext, ServerBridge

        return EvidenceContext if name == "EvidenceContext" else ServerBridge
    if name == "LogTailer":
        from ekchain.orchestrator.log_tailer import LogTailer

        return LogTailer
    raise AttributeError(f"module 'ekchain.orchestrator' has no attribute {name!r}")
