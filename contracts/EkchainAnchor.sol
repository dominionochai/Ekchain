// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title EkchainAnchor
/// @notice Tamper-evident audit ledger anchoring for Ekchain climate forensics
/// @dev Stores case audit log chain heads on-chain for tamper detection.
/// Each case can have multiple anchors appended over time.
contract EkchainAnchor {
    /// @notice Emitted when a new anchor is stored for a case
    event Anchored(
        bytes32 indexed caseId,
        bytes32 chainHead,
        uint32 entryCount,
        address indexed anchorer,
        uint256 timestamp
    );

    /// @notice A single anchor record
    struct AnchorRecord {
        bytes32 caseId;
        bytes32 chainHead;
        uint32 entryCount;
        address anchorer;
        uint256 timestamp;
    }

    /// @notice All anchors per case, append-only
    mapping(bytes32 => AnchorRecord[]) private _anchors;

    /// @notice Anchor a new chain head for a case
    /// @param caseId The 32-byte case identifier (sha256 of case id string)
    /// @param chainHead The 32-byte hash chain head (sha256 of last entry)
    /// @param entryCount Number of entries in the chain
    /// @param reportCid Optional IPFS CID of the full report
    function anchor(
        bytes32 caseId,
        bytes32 chainHead,
        uint32 entryCount,
        string calldata reportCid
    ) external {
        // Create new anchor record
        AnchorRecord memory record = AnchorRecord({
            caseId: caseId,
            chainHead: chainHead,
            entryCount: entryCount,
            anchorer: msg.sender,
            timestamp: block.timestamp
        });

        // Append to case's anchor list
        _anchors[caseId].push(record);

        // Emit event for off-chain indexing
        emit Anchored(caseId, chainHead, entryCount, msg.sender, block.timestamp);
    }

    /// @notice Get all anchors for a case
    /// @param caseId The 32-byte case identifier
    /// @return Array of AnchorRecord for the case
    function getAnchors(bytes32 caseId) external view returns (AnchorRecord[] memory) {
        return _anchors[caseId];
    }

    /// @notice Get the number of anchors for a case
    /// @param caseId The 32-byte case identifier
    /// @return Number of anchors stored for this case
    function anchorCount(bytes32 caseId) external view returns (uint256) {
        return _anchors[caseId].length;
    }

    /// @notice Get the latest anchor for a case
    /// @param caseId The 32-byte case identifier
    /// @return The most recent AnchorRecord, or empty if none
    function getLatestAnchor(bytes32 caseId) external view returns (AnchorRecord memory) {
        uint256 count = _anchors[caseId].length;
        if (count == 0) {
            return AnchorRecord(0, 0, 0, address(0), 0);
        }
        return _anchors[caseId][count - 1];
    }
}