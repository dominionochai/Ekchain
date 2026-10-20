"""Offline tests for Ekchain on-chain anchoring contract and client

using web3.py EthereumTesterProvider.
"""

from __future__ import annotations

import contextlib

import pytest
from eth_tester import EthereumTester, PyEVMBackend
from web3 import EthereumTesterProvider, Web3
from web3.middleware import ExtraDataToPOAMiddleware

from ekchain.chain.client import ChainClient, deploy_contract


@pytest.fixture()
def tester_w3() -> Web3:
    """Provide a Web3 instance backed by EthereumTester."""
    backend = PyEVMBackend()
    tester = EthereumTester(backend=backend)
    provider = EthereumTesterProvider(tester)
    w3 = Web3(provider)
    # Inject poa middleware if needed for certain EVM features
    with contextlib.suppress(Exception):
        w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    return w3


@pytest.fixture()
def deployer_account(tester_w3: Web3) -> tuple[str, str]:
    """Provide a funded deployer account."""
    private_key = "0x" + "1" * 64
    account = tester_w3.eth.account.from_key(private_key)
    # Fund account from tester genesis account
    funding_account = tester_w3.eth.accounts[0]
    tester_w3.eth.send_transaction(
        {
            "from": funding_account,
            "to": account.address,
            "value": tester_w3.to_wei(100, "ether"),
        }
    )
    return private_key, account.address


def test_contract_deployment_and_anchoring(
    tester_w3: Web3, deployer_account: tuple[str, str]
) -> None:
    """Test contract deployment, anchoring twice, event emission, anchorCount, and getAnchors."""
    private_key, account_address = deployer_account
    chain_id = tester_w3.eth.chain_id

    # 1. Deploy contract using deploy_contract helper with w3
    deploy_res = deploy_contract(private_key=private_key, chain_id=chain_id, w3=tester_w3)
    contract_address = deploy_res["contract_address"]
    assert contract_address

    # 2. Instantiate ChainClient with w3
    client = ChainClient(
        private_key=private_key,
        anchor_address=contract_address,
        chain_id=chain_id,
        w3=tester_w3,
    )

    case_id_str = "incident-001"
    head1 = "a" * 64
    head2 = "b" * 64

    # 3. Anchor twice
    res1 = client.anchor(case_id_str, head1, entry_count=10)
    assert res1["status"] == 1
    assert "tx_hash" in res1

    res2 = client.anchor(case_id_str, head2, entry_count=20)
    assert res2["status"] == 1

    # 4. Check anchorCount and getAnchors
    count = client.anchor_count(case_id_str)
    assert count == 2

    anchors = client.get_anchors(case_id_str)
    assert len(anchors) == 2
    assert anchors[0]["entry_count"] == 10
    assert anchors[1]["entry_count"] == 20
    assert anchors[0]["anchorer"].lower() == account_address.lower()
    assert anchors[1]["anchorer"].lower() == account_address.lower()

    latest = client.get_latest_anchor(case_id_str)
    assert latest is not None
    assert latest["entry_count"] == 20
    assert latest["chain_head"] == "0x" + head2


def test_chain_client_tx_urls() -> None:
    """Test tx_url() for Polygon Amoy (80002) and Sepolia (11155111)."""
    tx_hash = "0x1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef"

    # Test Polygon Amoy (80002)
    client_amoy = ChainClient(
        private_key="0x" + "1" * 64,
        anchor_address="0x1234567890123456789012345678901234567890",
        chain_id=80002,
        w3=Web3(),
    )
    url_amoy = client_amoy.tx_url(tx_hash)
    assert url_amoy == f"https://www.oklink.com/amoy/tx/{tx_hash}"

    # Test Sepolia (11155111)
    client_sepolia = ChainClient(
        private_key="0x" + "1" * 64,
        anchor_address="0x1234567890123456789012345678901234567890",
        chain_id=11155111,
        w3=Web3(),
    )
    url_sepolia = client_sepolia.tx_url(tx_hash)
    assert url_sepolia == f"https://sepolia.etherscan.io/tx/{tx_hash}"
