#!/usr/bin/env python3
"""Deploy EkchainAnchor contract to an EVM testnet.

Usage:
    EKCHAIN_RPC_URL=https://rpc-amoy.polygon.technology \\
    EKCHAIN_PRIVATE_KEY=your_private_key_without_0x \\
    EKCHAIN_CHAIN_ID=80002 \\
    python scripts/deploy_anchor.py

Or with .env file:
    python scripts/deploy_anchor.py

Requires:
    - EKCHAIN_RPC_URL: RPC endpoint (Alchemy, Infura, or public RPC)
    - EKCHAIN_PRIVATE_KEY: Deployer private key (without 0x prefix)
    - EKCHAIN_CHAIN_ID: 80002 (Polygon Amoy) or 11155111 (Sepolia)

Optional:
    EKCHAIN_ANCHOR_ADDRESS: If already deployed, verify instead of deploy
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Add src to path for local imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ekchain.chain.client import ChainClient, deploy_contract

FAUCET_URLS = {
    80002: "https://faucet.polygon.technology/ (Polygon Amoy faucet)",
    11155111: "https://sepoliafaucet.com/ (Sepolia faucet)",
    # Add more as needed
}

EXPLORER_BASE = {
    80002: "https://www.oklink.com/amoy/tx/",
    11155111: "https://sepolia.etherscan.io/tx/",
}


def check_balance(chain_client: ChainClient) -> float:
    """Check deployer balance in native token."""
    balance_wei = chain_client.w3.eth.get_balance(chain_client.account.address)
    return float(chain_client.w3.from_wei(balance_wei, "ether"))


def main() -> int:
    # Check for .env file
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        from dotenv import load_dotenv

        load_dotenv(env_path)

    # Check required env vars
    required = ["EKCHAIN_RPC_URL", "EKCHAIN_PRIVATE_KEY", "EKCHAIN_CHAIN_ID"]
    missing = [v for v in required if not os.environ.get(v)]
    if missing:
        missing_str = ", ".join(missing)
        print(f"Error: Missing required environment variables: {missing_str}", file=sys.stderr)
        print("Required:", file=sys.stderr)
        for v in required:
            print(f"  {v}", file=sys.stderr)
        print("\nOptional:", file=sys.stderr)
        print("  EKCHAIN_ANCHOR_ADDRESS (if already deployed)", file=sys.stderr)
        return 2

    rpc_url = os.environ["EKCHAIN_RPC_URL"]
    private_key = os.environ["EKCHAIN_PRIVATE_KEY"]
    chain_id = int(os.environ["EKCHAIN_CHAIN_ID"])
    anchor_address = os.environ.get("EKCHAIN_ANCHOR_ADDRESS")

    # Create a temporary client to check balance
    if not private_key.startswith("0x"):
        private_key = "0x" + private_key

    from web3 import Web3

    w3 = Web3(Web3.HTTPProvider(rpc_url))
    account = w3.eth.account.from_key(private_key)
    balance_wei = w3.eth.get_balance(account.address)
    balance_eth = w3.from_wei(balance_wei, "ether")

    print(f"Deployer: {account.address}")
    print(f"Balance: {balance_eth:.6f} ETH")
    print(f"Chain ID: {chain_id}")

    if balance_wei == 0:
        faucet = FAUCET_URLS.get(chain_id, "https://faucet.example.com/")
        print("\nERROR: Zero balance! Cannot deploy.", file=sys.stderr)
        print(f"Get testnet funds from: {faucet}", file=sys.stderr)
        return 1

    if balance_eth < 0.01:
        msg = f"\nWARNING: Low balance ({balance_eth:.6f} ETH). Deployment may fail."
        print(msg, file=sys.stderr)

    # If anchor address provided, verify instead of deploy
    if anchor_address:
        print(f"\nVerifying existing anchor at {anchor_address}...")
        try:
            client = ChainClient.from_env()
            count = client.anchor_count("test-case")
            print(f"Anchor contract verified. Existing anchors for 'test-case': {count}")
            return 0
        except Exception as e:
            print(f"Failed to verify anchor: {e}", file=sys.stderr)
            return 1

    # Deploy
    print("\nDeploying EkchainAnchor contract...")
    try:
        result = deploy_contract(rpc_url, private_key, chain_id)
        print("\n✓ Contract deployed successfully!")
        print(f"  Address: {result['contract_address']}")
        print(f"  TX Hash: {result['tx_hash']}")
        print(f"  Block:   {result['block_number']}")
        print(f"  Explorer: {result['explorer_url']}")

        # Save to .env.example hint
        print("\nAdd to your .env:")
        print(f"  EKCHAIN_ANCHOR_ADDRESS={result['contract_address']}")
        print(f"  EKCHAIN_CHAIN_ID={chain_id}")
        print(f"  EKCHAIN_RPC_URL={rpc_url}")

        return 0

    except Exception as e:
        print(f"\nERROR: Deployment failed: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
