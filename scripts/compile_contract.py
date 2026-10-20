#!/usr/bin/env python3
"""Compile EkchainAnchor.sol and emit ABI + bytecode as Python package data.

Run from repo root: python scripts/compile_contract.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

try:
    from solcx import compile_source, install_solc
except ImportError:
    print("py-solc-x not installed. Install with: pip install py-solc-x", file=sys.stderr)
    sys.exit(1)

# Solidity version to use
SOLC_VERSION = "0.8.20"

# Paths
CONTRACT_PATH = Path(__file__).resolve().parent.parent / "contracts" / "EkchainAnchor.sol"
ARTIFACTS_DIR = Path(__file__).resolve().parent.parent / "src" / "ekchain" / "chain" / "artifacts"
ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)

ABI_OUTPUT = ARTIFACTS_DIR / "EkchainAnchor.abi.json"
BYTECODE_OUTPUT = ARTIFACTS_DIR / "EkchainAnchor.bytecode.json"
METADATA_OUTPUT = ARTIFACTS_DIR / "EkchainAnchor.metadata.json"


def main() -> int:
    # Install solc if needed
    print(f"Installing solc {SOLC_VERSION}...")
    try:
        install_solc(SOLC_VERSION)
    except Exception as e:
        print(f"Warning: solc install failed (may already be installed): {e}")

    # Read contract source
    if not CONTRACT_PATH.exists():
        print(f"Contract not found at {CONTRACT_PATH}", file=sys.stderr)
        return 1

    source = CONTRACT_PATH.read_text(encoding="utf-8")

    # Compile
    print("Compiling EkchainAnchor.sol...")
    try:
        compiled = compile_source(
            source,
            output_values=["abi", "bin", "metadata"],
            solc_version=SOLC_VERSION,
            optimize=True,
            optimize_runs=200,
        )
    except Exception as e:
        print(f"Compilation failed: {e}", file=sys.stderr)
        return 1

    # Extract contract data (single contract in file)
    contract_key = list(compiled.keys())[0]
    contract_data = compiled[contract_key]

    abi = contract_data["abi"]
    bytecode = contract_data["bin"]
    metadata = json.loads(contract_data["metadata"])

    # Write outputs
    ABI_OUTPUT.write_text(json.dumps(abi, indent=2), encoding="utf-8")
    BYTECODE_OUTPUT.write_text(json.dumps({"bytecode": bytecode}, indent=2), encoding="utf-8")
    METADATA_OUTPUT.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    print(f"Wrote ABI to {ABI_OUTPUT}")
    print(f"Wrote bytecode to {BYTECODE_OUTPUT}")
    print(f"Wrote metadata to {METADATA_OUTPUT}")

    # Verify bytecode is not empty
    if not bytecode or bytecode == "0x":
        print("ERROR: Bytecode is empty!", file=sys.stderr)
        return 1

    print(f"Bytecode length: {len(bytecode)} chars")
    print("Compilation successful!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
