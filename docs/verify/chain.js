/**
 * Ekchain Hash Chain Verification - Core Logic
 *
 * This module implements the canonical hash chain computation and verification
 * that must match Python's ekchain.audit byte-for-byte.
 *
 * Canonical JSON format (matching Python's json.dumps):
 * - sort_keys=true
 * - separators=(",", ":") - no spaces
 * - ensure_ascii=false - no Unicode escaping
 *
 * Hash chain: entry_hash = SHA256(prev_hash + canonical_json(entry_without_entry_hash))
 * Genesis prev_hash = "0".repeat(64)
 */

// TextEncoder for UTF-8 encoding
const encoder = new TextEncoder();

/**
 * Canonical JSON stringification matching Python's json.dumps:
 * - sorted keys
 * - no whitespace (separators=(",", ":"))
 * - no ASCII escaping (ensure_ascii=false)
 */
export function canonicalJson(obj) {
    // Handle null/undefined
    if (obj === null || obj === undefined) {
        return 'null';
    }

    // Handle primitives
    const type = typeof obj;
    if (type === 'string') {
        return JSON.stringify(obj);
    }
    if (type === 'number') {
        // JSON doesn't distinguish int/float, but we must match Python's formatting
        // Python: 150 -> "150", 150.0 -> "150.0"
        // JavaScript: both serialize as "150"
        // We need to preserve the original if it's a float
        // Since we can't distinguish, we use the value as-is
        return String(obj);
    }
    if (type === 'boolean') {
        return obj ? 'true' : 'false';
    }

    // Handle arrays
    if (Array.isArray(obj)) {
        const items = obj.map(canonicalJson).join(',');
        return '[' + items + ']';
    }

    // Handle objects - sort keys
    const keys = Object.keys(obj).sort();
    const parts = keys.map(k => canonicalJson(k) + ':' + canonicalJson(obj[k]));
    return '{' + parts.join(',') + '}';
}

/**
 * Compute entry hash: SHA256(prev_hash + canonical_json(entry without entry_hash))
 * @param {string} prevHash - 64-char hex string
 * @param {Object} entry - Entry object (may contain entry_hash)
 * @returns {Promise<string>} 64-char hex string
 */
export async function computeEntryHash(prevHash, entry) {
    // Create copy without entry_hash
    const { entry_hash: _, ...entryForHash } = entry;
    const canonical = canonicalJson(entryForHash);
    const data = prevHash + canonical;

    // SHA-256 using Web Crypto API
    const msgBuffer = new TextEncoder().encode(data);
    const hashBuffer = await crypto.subtle.digest('SHA-256', msgBuffer);
    const hashArray = Array.from(new Uint8Array(hashBuffer));
    return hashArray.map(b => b.toString(16).padStart(2, '0')).join('');
}

/**
 * Verify a hash chain from an array of entries
 * @param {Array<Object>} entries - Array of entry objects (each with prev_hash, entry_hash)
 * @returns {Object} { ok, entries, head, firstBadIndex, reason, chained }
 */
export async function verifyChain(entries) {
    const GENESIS_PREV_HASH = '0'.repeat(64);

    let expectedPrevHash = GENESIS_PREV_HASH;
    let chained = true;
    let firstBadIndex = null;
    let reason = null;
    let entriesChecked = 0;
    let hasChainedEntries = false;

    for (let i = 0; i < entries.length; i++) {
        const entry = entries[i];

        // Check if this is a chained entry
        if (!('entry_hash' in entry) || !('prev_hash' in entry)) {
            chained = false;
            if (firstBadIndex === null) {
                firstBadIndex = i;
                reason = 'missing hash fields';
            }
            continue;
        }

        hasChainedEntries = true;

        // Verify prev_hash
        if (entry.prev_hash !== expectedPrevHash) {
            chained = false;
            if (firstBadIndex === null) {
                firstBadIndex = i;
                reason = `prev_hash mismatch: expected ${expectedPrevHash}, got ${entry.prev_hash}`;
            }
            continue;
        }

        // Verify entry_hash
        const computedHash = await computeEntryHash(entry.prev_hash, entry);
        if (entry.entry_hash !== computedHash) {
            chained = false;
            if (firstBadIndex === null) {
                firstBadIndex = i;
                reason = `entry_hash mismatch: expected ${computedHash}, got ${entry.entry_hash}`;
            }
            continue;
        }

        // All checks passed
        expectedPrevHash = entry.entry_hash;
        entriesChecked++;
    }

    const ok = chained && firstBadIndex === null;
    return {
        ok,
        entries: entriesChecked,
        head: expectedPrevHash,
        firstBadIndex,
        reason,
        chained: chained && hasChainedEntries
    };
}

/**
 * Parse JSONL text into array of entry objects
 * @param {string} jsonlText - JSONL content
 * @returns {Array<Object>} Array of parsed entry objects
 */
export function parseJsonl(jsonlText) {
    return jsonlText
        .split('\n')
        .map(line => line.trim())
        .filter(line => line.length > 0)
        .map(line => {
            try {
                return JSON.parse(line);
            } catch {
                return null;
            }
        })
        .filter(e => e !== null && typeof e === 'object');
}

/**
 * Verify a hash chain from JSONL text
 * @param {string} jsonlText - JSONL content
 * @returns {Promise<Object>} Verification result
 */
export async function verifyChainFromJsonl(jsonlText) {
    const entries = parseJsonl(jsonlText);
    return verifyChain(entries);
}

/**
 * Load audit log from file input
 * @param {File} file - File object from file picker
 * @returns {Promise<Array<Object>>} Parsed entries
 */
export async function loadAuditLog(file) {
    const text = await file.text();
    return parseJsonl(text);
}

/**
 * Format bytes as hex string
 * @param {Uint8Array|Array<number>} bytes
 * @returns {string}
 */
export function bytesToHex(bytes) {
    return Array.from(bytes).map(b => b.toString(16).padStart(2, '0')).join('');
}

/**
 * Format hash as 0x-prefixed hex
 * @param {string} hash - 64-char hex string
 * @returns {string}
 */
export function formatHash(hash) {
    return hash.startsWith('0x') ? hash : '0x' + hash;
}

/**
 * Verify anchor against on-chain record
 * @param {Object} params
 * @param {string} params.rpcUrl - RPC endpoint
 * @param {string} params.contractAddress - Contract address
 * @param {number} params.chainId - Chain ID
 * @param {string} params.caseId - Case ID string
 * @param {string} params.localChainHead - Local chain head (64 hex chars)
 * @returns {Promise<Object>} Verification result
 */
export async function verifyAnchorOnChain({ rpcUrl, contractAddress, chainId, caseId, localChainHead }) {
    // This would use ethers.js or web3.js to call the contract
    // For now, return a placeholder - the HTML page will use ethers.js directly
    throw new Error('verifyAnchorOnChain requires ethers.js - use the HTML page');
}