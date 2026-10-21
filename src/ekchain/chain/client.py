"""Ekchain on-chain anchoring client.

Provides a minimal web3.py wrapper for anchoring audit log chain heads
to the EkchainAnchor contract on EVM chains.
"""

from __future__ import annotations

import hashlib
import os
from typing import Any, cast

from web3 import Web3
from web3.contract import Contract
from web3.types import TxParams

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
        rpc_url: str | None = None,
        private_key: str | None = None,
        anchor_address: str | None = None,
        chain_id: int = 0,
        w3: Web3 | None = None,
    ) -> None:
        """Initialize the chain client.

        Either *rpc_url* or *w3* must be provided.  When *w3* is given
        (e.g. an ``EthereumTesterProvider`` for offline tests) the RPC
        connection and chain-ID checks are skipped, making the client
        usable without a live network while *chain_id* still controls
        ``tx_url`` output and the ``CHAIN_CONFIG`` lookup.

        Args:
            rpc_url: RPC endpoint URL (e.g., from Alchemy, Infura, or public RPC).
            private_key: Hex private key for signing (without 0x prefix, or with).
            anchor_address: Deployed EkchainAnchor contract address.
            chain_id: EVM chain ID (80002 for Polygon Amoy, 11155111 for Sepolia).
            w3: Pre-configured Web3 instance (for testing with EthereumTesterProvider).
        """
        self.chain_id = chain_id
        self.chain_config = CHAIN_CONFIG.get(chain_id, {})
        self.anchor_address: str = ""
        self.contract: Contract | None = None
        self.account: Any = None

        if w3 is not None:
            self.w3 = w3
            if anchor_address is not None:
                self.anchor_address = Web3.to_checksum_address(anchor_address)
                self.contract = self.w3.eth.contract(
                    address=self.anchor_address,
                    abi=EKCHAIN_ANCHOR_ABI,
                )
            if private_key is not None:
                if not private_key.startswith("0x"):
                    private_key = "0x" + private_key
                self.account = self.w3.eth.account.from_key(private_key)
            return

        if rpc_url is None:
            raise ValueError("Either rpc_url or w3 must be provided")

        if anchor_address is None or private_key is None:
            raise ValueError("anchor_address and private_key are required when rpc_url is given")

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
        self.contract = self.w3.eth.contract(
            address=self.anchor_address,
            abi=EKCHAIN_ANCHOR_ABI,
        )

    @classmethod
    def from_env(cls, anchor_address: str | None = None) -> ChainClient:
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
            "EKCHAIN_CHAIN_ID": "Chain ID (80002 or 11155111)",
        }
        if anchor_address is None:
            required["EKCHAIN_ANCHOR_ADDRESS"] = "Deployed contract address"

        missing = [k for k in required if not os.environ.get(k)]
        if missing:
            raise ValueError(
                f"Missing required environment variables: {', '.join(missing)}. "
                f"Required: {list(required.keys())}"
            )

        return cls(
            rpc_url=os.environ["EKCHAIN_RPC_URL"],
            private_key=os.environ["EKCHAIN_PRIVATE_KEY"],
            anchor_address=anchor_address or os.environ["EKCHAIN_ANCHOR_ADDRESS"],
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
        clean_hex = chain_head_hex[2:] if chain_head_hex.startswith("0x") else chain_head_hex
        chain_head = bytes.fromhex(clean_hex)

        if len(chain_head) != 32:
            raise ValueError("chain_head must be 32 bytes (64 hex chars)")

        assert self.contract is not None
        # Build transaction
        nonce = self.w3.eth.get_transaction_count(self.account.address)
        tx_params = TxParams(
            {
                "from": self.account.address,
                "nonce": nonce,
                "chainId": self.chain_id,
                "gas": 300000,
                "maxFeePerGas": self.w3.to_wei(40, "gwei"),
                "maxPriorityFeePerGas": self.w3.to_wei(30, "gwei"),
            }
        )
        tx = self.contract.functions.anchor(
            case_id,
            "0x" + chain_head.hex(),
            entry_count,
            report_cid,
        ).build_transaction(tx_params)

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
        assert self.contract is not None
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
        assert self.contract is not None
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
        assert self.contract is not None
        return cast(int, self.contract.functions.anchorCount(case_id).call())

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

        if local_chain_head_hex.startswith("0x"):
            local_head = local_chain_head_hex
        else:
            local_head = "0x" + local_chain_head_hex

        match = local_head.lower() == on_chain_head.lower()

        return {
            "match": match,
            "reason": ("Hashes match" if match else "Hash mismatch: heads differ"),
            "local_head": local_head,
            "on_chain_head": on_chain_head,
            "block_timestamp": latest["timestamp"],
            "explorer_url": None,
        }


def deploy_contract(
    rpc_url: str | None = None,
    private_key: str | None = None,
    chain_id: int = 0,
    w3: Web3 | None = None,
) -> dict[str, Any]:
    """Deploy a new EkchainAnchor contract.

    Either *rpc_url* or *w3* must be provided.  When *w3* is given (e.g.
    an ``EthereumTesterProvider`` for offline tests) the RPC connection
    and chain-ID checks are skipped.

    Args:
        rpc_url: RPC endpoint URL
        private_key: Deployer's private key
        chain_id: Chain ID (80002 or 11155111)
        w3: Pre-configured Web3 instance (for testing)

    Returns:
        Dict with contract_address, tx_hash, explorer_url
    """
    if w3 is not None:
        if private_key is None:
            raise ValueError("private_key is required")
    elif rpc_url is not None:
        w3 = Web3(Web3.HTTPProvider(rpc_url))
        if not w3.is_connected():
            raise ConnectionError(f"Failed to connect to RPC at {rpc_url}")
        if private_key is None:
            raise ValueError("private_key is required")
    else:
        raise ValueError("Either rpc_url or w3 must be provided")

    if not private_key.startswith("0x"):
        private_key = "0x" + private_key
    account = w3.eth.account.from_key(private_key)

    # Load bytecode
    from ekchain.chain.artifacts import EKCHAIN_ANCHOR_BYTECODE

    bytecode = EKCHAIN_ANCHOR_BYTECODE["bytecode"]

    # Deploy
    contract = w3.eth.contract(abi=EKCHAIN_ANCHOR_ABI, bytecode=bytecode)
    nonce = w3.eth.get_transaction_count(account.address)

    tx_params = TxParams(
        {
            "from": account.address,
            "nonce": nonce,
            "chainId": chain_id,
            "gas": 1200000,
            "maxFeePerGas": w3.to_wei(40, "gwei"),
            "maxPriorityFeePerGas": w3.to_wei(30, "gwei"),
        }
    )
    tx = contract.constructor().build_transaction(tx_params)

    signed_tx = account.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)

    contract_address = receipt["contractAddress"]
    if not contract_address:
        raise RuntimeError("Contract deployment failed - no address in receipt")

    chain_config = CHAIN_CONFIG.get(chain_id, {})
    explorer_base = chain_config.get("explorer_base", "https://etherscan.io/tx/")

    return {
        "contract_address": contract_address,
        "tx_hash": "0x" + receipt["transactionHash"].hex(),
        "block_number": receipt["blockNumber"],
        "explorer_url": explorer_base + "0x" + receipt["transactionHash"].hex(),
        "deployer": account.address,
    }
