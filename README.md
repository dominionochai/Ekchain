# EKCHAIN: Autonomous Multi-Agent Climate Forensics Platform

> **Track Alignment:** Track 4: Climate Data & Environmental Monitoring  
> **Event:** IEEE ClimateChain Global Hackathon 2026  
> **Deadline:** October 25, 2026 @ 3:00 PM GMT+1

## Executive Pitch

> “Greenwashing and carbon market fraud don't happen because humanity lacks sensors—they happen because nobody audits the telemetry. Industrial emitters spoof IoT sensor streams, report fake emission drops, or register double-counted offset credits.
>
> EKCHAIN is an autonomous multi-agent climate forensics engine. Built on a deterministic 5-phase orchestration loop with multi-role agent isolation, EKCHAIN treats climate telemetry (IoT streams, satellite imagery, industrial SCADA logs, carbon credit ledgers) as forensic evidence—verifying data integrity, running adversarial spoof checks, and anchoring tamper-proof audit trails directly onto the blockchain.”

## Architecture Transformation Map

We adapt a host-based Digital Forensics and Incident Response (DFIR) architecture into a climate data verification pipeline by swapping the tool execution allowlist while preserving the core orchestration engine:

| Original DFIR | Eco-Forensics Repurpose |
| --- | --- |
| Disk & Memory Dumps | Satellite Spectral Data (Sentinel/Landsat) |
| Network PCAPs (Tshark/Zeek) | Industrial SCADA & PLC System Logs |
| Malware & EVTX Parsers | Modbus / IoT Telemetry Packet Capture |
| Host Forensic Evidence | Carbon Credit Registry APIs (Verra/Gold) |

## Key Capabilities

- **Multi-Role AI Isolation:** separation between Planner (Opus), Executor (Haiku), and Analyst (Opus)
- **5-Phase Gated Workflow:** CATALOG → EXTRACTION → CROSS-SYSTEM → ALTERNATIVE_NARRATIVE → REPORT
- **Adversarial Critique:** Phase 4 actively tests counter-hypotheses to eliminate hallucinated conclusions
- **On-Chain Data Provenance:** cryptographic SHA-256 audit ledger anchored to EVM smart contracts
- **Cost-aware design:** the CATALOG gate halts the case immediately if evidence is missing or unreadable, saving compute

## The 5-Phase Gated Forensic Loop

### Phase 1 — CATALOG (single agent gate)

Scans input evidence directories containing factory IoT sensor streams, satellite atmospheric feeds (CO2 / CH4), SCADA log files, and carbon credit registry entries.

**Gate check:** identifies file types and integrity; if evidence is missing or unreadable, the case halts immediately.

### Phase 2 — EXTRACTION (multi-role cycle)

- **Planner (`claude-opus-5-5`):** constructs a structured tool execution strategy based on detected climate evidence
- **Executor (`claude-haiku-5-5`):** executes specific MCP tools (`parse_iot_telemetry`, `check_sensor_drift`, `scada_log_parser`); off-role tools strictly dropped via tool allowlists
- **Analyst (`claude-opus-5-5`):** interprets outputs, flags data anomalies, assigns IPCC/UNFCCC vulnerability identifiers, can request bounded follow-up execution rounds

### Phase 3 — CROSS-SYSTEM (correlation gate)

Correlates ground-level factory telemetry against overhead satellite atmospheric measurements.

**Gate check:** requires findings to map against the standard IPCC/UNFCCC climate anomaly taxonomy to pass.

### Phase 4 — ALTERNATIVE_NARRATIVE (the adversarial review loop)

Tests competing hypotheses before issuing a report. Examples:

- “Was the reported 40% emission drop genuine, or was the plant offline for unscheduled maintenance?”
- “Did the facility reduce emissions, or was the physical IoT sensor line tampered with using an inline resistor?”

**Outcome:** prevents AI hallucinations and false-positive fraud allegations.

### Phase 5 — REPORT & ON-CHAIN STATE ANCHOR

Compiles the complete forensic autopsy into a structured Jinja2 report and requires calling `finalize_report`. Calculates an append-only SHA-256 hash-chain over the SQLite/JSONL audit log and anchors the final state root to an EVM Testnet smart contract (e.g., Polygon Amoy / Sepolia).

## Technical Highlights

- **IEEE 2418 Alignment:** adheres to the IEEE frameworks for Blockchain and Distributed Ledger Technology (architecture per 2418.1, data-provenance angle) by maintaining tamper-proof data provenance
- **Multi-Role Separation:** prevents the executor model from altering planning context or accessing unauthorized tools
- **Cryptographic Tamper-Proofing:** upgrades standard JSONL logging with a cryptographic SHA-256 hash-chain, guaranteeing investigation records cannot be modified after the fact

## Quickstart

```bash
# 1. System setup
git clone https://github.com/dominionochai/Ekchain.git && cd Ekchain
pip install -e .

# 2. Verify installation
ekchain --help
```

## Tamper-Evident Ledger and On-Chain Anchoring

EKCHAIN implements a robust audit trail designed to align with IEEE 2418 principles for trustworthy distributed data provenance.

### Hash Formula
Audit log entries are cryptographically hash-chained. Each log entry incorporates:
`entry_hash = sha256(prev_hash + canonical_json(entry without entry_hash))`
where `prev_hash` for the genesis entry is `0000000000000000000000000000000000000000000000000000000000000000` (64 zeros), and `canonical_json` serializes the dictionary with sorted keys and no whitespace (`separators=(",", ":")`).

### CLI Commands
- **Verify Local Ledger:**
  ```bash
  ekchain verify-ledger <case_id> --db-dir DIR
  ```
  Verifies the cryptographic hash chain of `<case_id>.audit.jsonl`, reporting total entries and final head hash. Exits nonzero and indicates the first bad line if tampered.

- **Anchor Head On-Chain:**
  ```bash
  ekchain anchor <case_id> --db-dir DIR
  ```
  Verifies the ledger integrity, anchors the current chain head on the configured EVM testnet smart contract, outputs transaction details and explorer URL, and writes `<case_id>.anchor.json` next to the database.

- **Verify On-Chain Anchor:**
  ```bash
  ekchain verify-anchor <case_id> --db-dir DIR
  ```
  Recomputes the local audit log head, queries the on-chain contract record, and reports `MATCH` or `MISMATCH`.

- **Export Case Summary:**
  ```bash
  ekchain export-case <case_id> --db-dir DIR
  ```
  Exports redacted case metadata, evidence sources, findings, and audit stats into `<case_id>.case.json` with absolute paths stripped.

### Environment Variables
To interact with EVM testnets, configure the following environment variables:
- `EKCHAIN_RPC_URL`: JSON-RPC endpoint URL
- `EKCHAIN_PRIVATE_KEY`: Hexadecimal private key for signing transactions (without or with `0x` prefix; never logged or written)
- `EKCHAIN_ANCHOR_ADDRESS`: Deployed `EkchainAnchor` contract address
- `EKCHAIN_CHAIN_ID`: EVM chain ID (`80002` for Polygon Amoy, `11155111` for Sepolia)

### Testnet Funds
Deploying contracts and submitting anchor transactions on testnets requires testnet native tokens (ETH or MATIC). Obtain testnet funds from official network faucets (such as the Polygon Amoy Faucet or Sepolia Faucet) prior to anchoring.

## Live Demo Video Script (3-5 minutes)

- **0:00 - 0:45 (Problem):** demonstrate a corporate dashboard reporting fake zero-emissions; highlight the vulnerability of un-audited IoT climate feeds
- **0:45 - 2:00 (Execution):** run `ekchain investigate ./evidence`; show terminal output moving through CATALOG, EXTRACTION, and CROSS-SYSTEM correlation
- **2:00 - 3:00 (The Climax):** highlight Phase 4 (ALTERNATIVE NARRATIVE); show the LLM prompt actively proving an IoT sensor was tampered with by cross-referencing satellite thermal logs
- **3:00 - 4:00 (Verification):** show Phase 5 generating the SHA-256 hash chain and publishing the transaction root to the smart contract
