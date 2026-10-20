"""Tests for Ekchain CLI chain anchoring and verification commands using eth-tester."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner
from eth_tester import EthereumTester, PyEVMBackend
from web3 import EthereumTesterProvider, Web3

from ekchain.audit import AuditLog
from ekchain.chain.client import deploy_contract
from ekchain.cli import cli


@pytest.fixture()
def evm_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Set up offline EVM test environment and monkeypatch ChainClient for CLI tests."""
    backend = PyEVMBackend()
    tester = EthereumTester(backend=backend)
    provider = EthereumTesterProvider(tester)
    w3 = Web3(provider)

    private_key = "0x" + "1" * 64
    deployer_account = w3.eth.account.from_key(private_key)
    funding_account = w3.eth.accounts[0]
    w3.eth.send_transaction(
        {
            "from": funding_account,
            "to": deployer_account.address,
            "value": w3.to_wei(100, "ether"),
        }
    )

    chain_id = w3.eth.chain_id
    deploy_res = deploy_contract(private_key=private_key, chain_id=chain_id, w3=w3)
    contract_address = deploy_res["contract_address"]

    monkeypatch.setenv("EKCHAIN_RPC_URL", "http://localhost:8545")
    monkeypatch.setenv("EKCHAIN_PRIVATE_KEY", private_key[2:])
    monkeypatch.setenv("EKCHAIN_ANCHOR_ADDRESS", contract_address)
    monkeypatch.setenv("EKCHAIN_CHAIN_ID", str(chain_id))
    monkeypatch.setenv("EKCHAIN_DB_DIR", str(tmp_path))

    from ekchain.chain.client import ChainClient

    def mock_from_env(anchor_address: str | None = None) -> ChainClient:
        return ChainClient(
            private_key=private_key,
            anchor_address=anchor_address or contract_address,
            chain_id=chain_id,
            w3=w3,
        )

    monkeypatch.setattr(ChainClient, "from_env", mock_from_env)
    return tmp_path


def test_verify_ledger_valid(evm_setup: Path) -> None:
    """Test verify-ledger command with a valid audit log."""
    db_dir = evm_setup
    case_id = "case-valid"
    log_path = db_dir / f"{case_id}.audit.jsonl"
    audit = AuditLog(log_path)
    audit.log_tool_call("tc_1", "trivy_report", {"source_path": "foo"}, "sha256:abc")

    runner = CliRunner()
    result = runner.invoke(cli, ["verify-ledger", case_id, "--db-dir", str(db_dir)])
    assert result.exit_code == 0
    assert "Ledger valid" in result.output
    assert "Entries: 1" in result.output


def test_verify_ledger_invalid(evm_setup: Path) -> None:
    """Test verify-ledger command with a tampered audit log."""
    db_dir = evm_setup
    case_id = "case-invalid"
    log_path = db_dir / f"{case_id}.audit.jsonl"
    audit = AuditLog(log_path)
    audit.log_tool_call("tc_1", "trivy_report", {"source_path": "foo"}, "sha256:abc")

    # Tamper with the log line
    lines = log_path.read_text().splitlines()
    entry = json.loads(lines[0])
    entry["params"] = {"source_path": "tampered"}
    # Re-save with wrong hash
    log_path.write_text(json.dumps(entry) + "\n")

    runner = CliRunner()
    result = runner.invoke(cli, ["verify-ledger", case_id, "--db-dir", str(db_dir)])
    assert result.exit_code != 0
    assert "Ledger invalid" in result.output


def test_anchor_and_verify_anchor_workflow(evm_setup: Path) -> None:
    """Test anchor command, anchor.json generation, verify-anchor MATCH, and post-edit MISMATCH."""
    db_dir = evm_setup
    case_id = "case-anchor"
    log_path = db_dir / f"{case_id}.audit.jsonl"
    audit = AuditLog(log_path)
    audit.log_tool_call("tc_1", "trivy_report", {"source_path": "bar"}, "sha256:123")

    runner = CliRunner()

    # 1. Anchor command
    result = runner.invoke(cli, ["anchor", case_id, "--db-dir", str(db_dir)])
    assert result.exit_code == 0, result.output
    assert "TX Hash" in result.output

    anchor_json = db_dir / f"{case_id}.anchor.json"
    assert anchor_json.exists()
    data = json.loads(anchor_json.read_text())
    assert data["case_id"] == case_id
    assert "tx_hash" in data
    assert "entry_count" in data
    assert "contract_address" in data

    # 2. Verify anchor MATCH
    v_res = runner.invoke(cli, ["verify-anchor", case_id, "--db-dir", str(db_dir)])
    assert v_res.exit_code == 0, v_res.output
    assert "MATCH" in v_res.output

    # 3. Edit log after anchoring (tamper) to cause MISMATCH
    audit.log_tool_call("tc_2", "dependency_diff", {"source_path": "baz"}, "sha256:456")

    # 4. Verify anchor MISMATCH
    m_res = runner.invoke(cli, ["verify-anchor", case_id, "--db-dir", str(db_dir)])
    assert m_res.exit_code != 0
    assert "MISMATCH" in m_res.output


def test_anchor_refuses_tampered_log(evm_setup: Path) -> None:
    """Test that anchor command refuses to anchor a tampered audit log."""
    db_dir = evm_setup
    case_id = "case-tampered"
    log_path = db_dir / f"{case_id}.audit.jsonl"
    audit = AuditLog(log_path)
    audit.log_tool_call("tc_1", "trivy_report", {"source_path": "foo"}, "sha256:abc")

    # Tamper log
    lines = log_path.read_text().splitlines()
    entry = json.loads(lines[0])
    entry["params"] = {"source_path": "tampered"}
    log_path.write_text(json.dumps(entry) + "\n")

    runner = CliRunner()
    result = runner.invoke(cli, ["anchor", case_id, "--db-dir", str(db_dir)])
    assert result.exit_code != 0
    assert "Audit log verification failed" in result.output


def test_cli_missing_env_exit_2(evm_setup: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test missing environment variables cause exit code 2 and name the variables."""
    db_dir = evm_setup
    case_id = "case-env"
    log_path = db_dir / f"{case_id}.audit.jsonl"
    audit = AuditLog(log_path)
    audit.log_tool_call("tc_1", "trivy_report", {"source_path": "foo"}, "sha256:abc")

    monkeypatch.delenv("EKCHAIN_RPC_URL", raising=False)
    monkeypatch.delenv("EKCHAIN_PRIVATE_KEY", raising=False)

    runner = CliRunner()
    result = runner.invoke(cli, ["anchor", case_id, "--db-dir", str(db_dir)])
    assert result.exit_code == 2
    assert "EKCHAIN_RPC_URL" in result.output
    assert "EKCHAIN_PRIVATE_KEY" in result.output


def test_cli_missing_web3_exit_2(evm_setup: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test missing web3 dependency prints clear message and exits 2."""
    db_dir = evm_setup
    case_id = "case-noweb3"
    log_path = db_dir / f"{case_id}.audit.jsonl"
    audit = AuditLog(log_path)
    audit.log_tool_call("tc_1", "trivy_report", {"source_path": "foo"}, "sha256:abc")

    import sys

    monkeypatch.setitem(sys.modules, "ekchain.chain.client", None)

    runner = CliRunner()
    result = runner.invoke(cli, ["anchor", case_id, "--db-dir", str(db_dir)])
    assert result.exit_code == 2
    assert "web3 not installed" in result.output
    assert "pip install 'ekchain[chain]'" in result.output
