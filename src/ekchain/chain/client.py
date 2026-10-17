"""Ekchain on-chain anchoring client.

Provides a minimal web3.py wrapper for anchoring audit log chain heads
to the EkchainAnchor contract on EVM chains.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from web3 import Web3
from web3.contract import Contract

from ekchain.chain.artifacts import EKCHAIN_ANCHOR_ABI


# EVM chain configurations
CHAIN_CONFIG = {
    80002: {  # Polygon Amoy
        "name": "Polygon Amoy",
        "rpc_env": "EKCHAIN_RPC_URL_AMOY",
        "explorer_base": "https://www.oklink.com/amoy/tx/",
    },
    11155111: {  # Sepolia
        "name": "Sepolia",
        "rpc_env": "EKCHAIN_RPC_URL_SEPOLIA",
        "explorer_base": "https://sepolia.etherscan.io/tx/",
    },
}


class ChainClient:
    """Client for interacting with the EkchainAnchor contract."""

    def __init__(
        self,
        rpc_url: str,
        private_key: str,
        anchor_address: str,
        chain_id: int,
    ) -> None:
        """Initialize the chain client.

        Args:
            rpc_url: RPC endpoint URL (e.g., from Alchemy, Infura, or public RPC)
            private_key: Hex private key for signing (without 0x prefix, or with)
            anchor_address: Deployed EkchainAnchor contract address
            chain_id: EVM chain ID (80002 for Polygon Amoy, 11155111 for Sepolia)
        """
        self.chain_id = chain_id
        self.chain_config = CHAIN_CONFIG.get(chain_id, {})
        self.anchor_address = Web3.to_checksum_address(anchor_address)

        # Initialize Web3
        self.w3 = Web3(Web3.HTTPProvider(rpc_url))
        if not self.w3.is_connected():
            raise ConnectionError(f"Failed to connect to RPC at {rpc_url}")

        # Verify chain ID matches
        actual_chain_id = self.w3.eth.chain_id
        if actual_chain_id != chain_id:
            raise ValueError(f"Chain ID mismatch: expected {chain_id}, got {actual_chain_id}")

        # Setup account
        if not private_key.startswith("0x"):
            private_key = "0x" + private_key
        self.account = self.w3.eth.account.from_key(private_key)

        # Load contract
        self.contract: Contract = self.w3.eth.contract(
            address=self.anchor_address,
            abi=EKCHAIN_ANCHOR_ABI,
        )

    @classmethod
    def from_env(cls) -> "ChainClient":
        """Create client from environment variables.

        Required env vars:
            EKCHAIN_RPC_URL: RPC endpoint URL
            EKCHAIN_PRIVATE_KEY: Hex private key (without 0x prefix)
            EKCHAIN_ANCHOR_ADDRESS: Deployed contract address
            EKCHAIN_CHAIN_ID: Chain ID (80002 or 11155111)

        Raises:
            ValueError: If any required env var is missing
        """
        required = {
            "EKCHAIN_RPC_URL": "RPC endpoint URL",
            "EKCHAIN_PRIVATE_KEY": "Private key for signing",
            "EKCHAIN_ANCHOR_ADDRESS": "Deployed contract address",
            "EKCHAIN_CHAIN_ID": "Chain ID (80002 or 11155111)",
        }

        missing = [k for k in required if not os.environ.get(k)]
        if missing:
            raise ValueError(
                f"Missing required environment variables: {', '.join(missing)}. "
                f"Required: {list(required.keys())}"
            )

        return cls(
            rpc_url=os.environ["EKCHAIN_RPC_URL"],
            private_key=os.environ["EKCHAIN_PRIVATE_KEY"],
            anchor_address=os.environ["EKCHAIN_ANCHOR_ADDRESS"],
            chain_id=int(os.environ["EKCHAIN_CHAIN_ID"]),
        )

    def _case_id_from_string(self, case_id_str: str) -> bytes:
        """Derive 32-byte case ID from case ID string."""
        return hashlib.sha256(case_id_str.encode("utf-8")).digest()

    def anchor(
        self,
        case_id_str: str,
        chain_head_hex: str,
        entry_count: int,
        report_cid: str = "",
    ) -> dict[str, Any]:
        """Anchor a new chain head for a case.

        Args:
            case_id_str: Human-readable case ID (e.g., "my-case-123")
            chain_head_hex: 64-char hex string of the chain head hash
            entry_count: Number of entries in the audit log chain
            report_cid: Optional IPFS CID of the full report

        Returns:
            Dict with tx_hash, block_number, gas_used, explorer_url
        """
        case_id = self._case_id_from_string(case_id_str)
        chain_head = bytes.fromhex(chain_head_hex) if not chain_head_hex.startswith("0x") else bytes.fromhex(chain_head_hex[2:])

        if len(chain_head) != 32:
            raise ValueError("chain_head must be 32 bytes (64 hex chars)")

        # Build transaction
        nonce = self.w3.eth.get_transaction_count(self.account.address)
        tx = self.contract.functions.anchor(
            case_id,
            "0x" + chain_head.hex(),
            entry_count,
            report_cid,
        ).build_transaction({
            "from": self.account.address,
            "nonce": nonce,
            "chainId": self.chain_id,
            "gas": 300000,
            "maxFeePerGas": self.w3.eth.gas_price * 2,
            "maxPriorityFeePerGas": self.w3.eth.gas_price,
        })

        # Sign and send
        signed_tx = self.account.sign_transaction(tx)
        tx_hash = self.w3.eth.send_raw_transaction(signed_tx.raw_transaction)

        # Wait for receipt
        receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash)

        return {
            "tx_hash": "0x" + receipt["transactionHash"].hex(),
            "block_number": receipt["blockNumber"],
            "gas_used": receipt["gasUsed"],
            "explorer_url": self.tx_url("0x" + receipt["transactionHash"].hex()),
            "status": receipt["status"],
        }

    def get_anchors(self, case_id_str: str) -> list[dict[str, Any]]:
        """Retrieve all anchors for a case.

        Args:
            case_id_str: Human-readable case ID

        Returns:
            List of anchor records with chain_head, entry_count, anchorer, timestamp
        """
        case_id = self._case_id_from_string(case_id_str)
        anchors = self.contract.functions.getAnchors(case_id).call()

        return [
            {
                "case_id": "0x" + a[0].hex() if isinstance(a[0], bytes) else a[0],
                "chain_head": "0x" + a[1].hex() if isinstance(a[1], bytes) else a[1],
                "entry_count": a[2],
                "anchorer": a[3],
                "timestamp": a[4],
            }
            for a in anchors
        ]

    def get_latest_anchor(self, case_id_str: str) -> dict[str, Any] | None:
        """Get the most recent anchor for a case.

        Args:
            case_id_str: Human-readable case ID

        Returns:
            Latest anchor record or None if no anchors exist
        """
        case_id = self._case_id_from_string(case_id_str)
        anchor = self.contract.functions.getLatestAnchor(case_id).call()

        if anchor[0] == b"\x00" * 32:
            return None

        return {
            "case_id": "0x" + anchor[0].hex() if isinstance(anchor[0], bytes) else anchor[0],
            "chain_head": "0x" + anchor[1].hex() if isinstance(anchor[1], bytes) else anchor[1],
            "entry_count": anchor[2],
            "anchorer": anchor[3],
            "timestamp": anchor[4],
        }

    def anchor_count(self, case_id_str: str) -> int:
        """Get the number of anchors for a case."""
        case_id = self._case_id_from_string(case_id_str)
        return self.contract.functions.anchorCount(case_id).call()

    def tx_url(self, tx_hash: str) -> str:
        """Generate explorer URL for a transaction hash."""
        base = self.chain_config.get("explorer_base", "")
        if base:
            return base + tx_hash
        return f"https://etherscan.io/tx/{tx_hash}"

    def verify_anchor(self, case_id_str: str, local_chain_head_hex: str) -> dict[str, Any]:
        """Verify an anchor against the on-chain record.

        Args:
            case_id_str: Human-readable case ID
            local_chain_head_hex: Local chain head hash (64 hex chars)

        Returns:
            Dict with match status, local head, on-chain head, block timestamp, explorer URL
        """
        latest = self.get_latest_anchor(case_id_str)
        if latest is None:
            return {
                "match": False,
                "reason": "No on-chain anchor found for this case",
                "local_head": local_chain_head_hex,
                "on_chain_head": None,
                "block_timestamp": None,
                "explorer_url": None,
            }

        on_chain_head = latest["chain_head"]
        if not on_chain_head.startswith("0x"):
            on_chain_head = "0x" + on_chain_head

        local_head = local_chain_head_hex if local_chain_head_hex.startswith("0x") else "0x" + local_chain_head_hex

        match = local_head.lower() == on_chain_head.lower()

        return {
            "match": match,
            "reason": "Hashes match" if match else "Hash mismatch: local and on-chain heads differ",
            "local_head": local_head,
            "on_chain_head": on_chain_head,
            "block_timestamp": latest["timestamp"],
            "explorer_url": self.tx_url(latest.get("tx_hash", "")) if latest.get("tx_hash") else None,
        }


def deploy_contract(
    rpc_url: str,
    private_key: str,
    chain_id: int,
) -> dict[str, Any]:
    """Deploy a new EkchainAnchor contract.

    Args:
        rpc_url: RPC endpoint URL
        private_key: Deployer's private key
        chain_id: Chain ID (80002 or 11155111)

    Returns:
        Dict with contract_address, tx_hash, explorer_url
    """
    w3 = Web3(Web3.HTTPProvider(rpc_url))
    if not w3.is_connected():
        raise ConnectionError(f"Failed to connect to RPC at {rpc_url}")

    actual_chain_id = w3.eth.chain_id
    if actual_chain_id != chain_id:
        raise ValueError(f"Chain ID mismatch: expected {chain_id}, got {actual_chain_id}")

    if not private_key.startswith("0x"):
        private_key = "0x" + private_key
    account = w3.eth.account.from_key(private_key)

    # Load bytecode
    from ekchain.chain.artifacts import EKCHAIN_ANCHOR_BYTECODE
    bytecode = EKCHAIN_ANCHOR_BYTECODE["bytecode"]

    # Deploy
    contract = w3.eth.contract(abi=EKCHAIN_ANCHOR_ABI, bytecode=bytecode)
    nonce = w3.eth.get_transaction_count(account.address)

    tx = contract.constructor().build_transaction({
        "from": account.address,
        "nonce": nonce,
        "chainId": chain_id,
        "gas": 5000000,
        "maxFeePerGas": w3.eth.gas_price * 2,
        "maxPriorityFeePerGas": w3.eth.gas_price,
    })

    signed_tx = account.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)

    contract_address = receipt["contractAddress"]
    if not contract_address:
        raise RuntimeError("Contract deployment failed - no address in receipt")

    chain_config = CHAIN_CONFIG.get(chain_id, {})
    explorer_base = chain_config.get("explorer_base", f"https://etherscan.io/tx/")

    return {
        "contract_address": contract_address,
        "tx_hash": "0x" + receipt["transactionHash"].hex(),
        "block_number": receipt["blockNumber"],
        "explorer_url": explorer_base + "0x" + receipt["transactionHash"].hex(),
        "deployer": account.address,
    }