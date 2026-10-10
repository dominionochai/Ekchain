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

## Using other model providers

Default configurations use Claude models (set `ANTHROPIC_API_KEY`). For other providers, Ekchain supports proxy routing via LiteLLM using `--proxy-config`.

Example using Google Gemini 3.8 Flash:

```bash
# 1. Install LiteLLM proxy support
pip install "litellm[proxy]"

# 2. Set the provider API key
export GEMINI_API_KEY=...

# 3. Verify provider connectivity and capabilities
python scripts/check_provider.py --proxy-config config/gemini.litellm.yaml --model openai/gemini-3.8-flash

# 4. Run an investigation using Gemini
ekchain investigate <evidence> <case-id> --proxy-config config/gemini.litellm.yaml --model openai/gemini-3.8-flash --workers 1 --no-thinking
```

Note: Other providers are supported through LiteLLM and results vary by model; Claude is the reference configuration.

## Live Demo Video Script (3-5 minutes)

- **0:00 - 0:45 (Problem):** demonstrate a corporate dashboard reporting fake zero-emissions; highlight the vulnerability of un-audited IoT climate feeds
- **0:45 - 2:00 (Execution):** run `ekchain investigate ./evidence`; show terminal output moving through CATALOG, EXTRACTION, and CROSS-SYSTEM correlation
- **2:00 - 3:00 (The Climax):** highlight Phase 4 (ALTERNATIVE NARRATIVE); show the LLM prompt actively proving an IoT sensor was tampered with by cross-referencing satellite thermal logs
- **3:00 - 4:00 (Verification):** show Phase 5 generating the SHA-256 hash chain and publishing the transaction root to the smart contract
