"""Ekchain on-chain anchoring package."""

from __future__ import annotations

from ekchain.chain.artifacts import (
    EKCHAIN_ANCHOR_ABI,
    EKCHAIN_ANCHOR_BYTECODE,
    EKCHAIN_ANCHOR_METADATA,
)
from ekchain.chain.client import ChainClient, deploy_contract

__all__ = [
    "ChainClient",
    "deploy_contract",
    "EKCHAIN_ANCHOR_ABI",
    "EKCHAIN_ANCHOR_BYTECODE",
    "EKCHAIN_ANCHOR_METADATA",
]
