#!/usr/bin/env node
/**
 * Ekchain Verifier Test Suite
 *
 * Tests the chain.js logic against test vectors from tests/vectors/chain_vectors.json
 * Run with: node docs/verify/verify.test.mjs
 */

import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

// Import the chain.js module
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const chainModule = await import('./chain.js');

const {
    canonicalJson,
    computeEntryHash,
    verifyChain,
    parseJsonl,
    verifyChainFromJsonl
} = chainModule;

function loadVectors() {
    const vectorsPath = path.resolve(__dirname, '../../tests/vectors/chain_vectors.json');
    const content = fs.readFileSync(vectorsPath, 'utf-8');
    return JSON.parse(content).vectors;
}

async function runTests() {
    const vectors = loadVectors();
    let passed = 0;
    let failed = 0;

    console.log('Running verifier tests against chain_vectors.json...\n');

    for (const v of vectors) {
        console.log(`Testing: ${v.name}`);
        console.log(`  Description: ${v.description}`);
        console.log(`  Entries: ${v.entries.length}`);

        // Test 1: Parse entries and verify hashes
        let prevHash = '0'.repeat(64);
        let allHashesMatch = true;

        for (let i = 0; i < v.entries.length; i++) {
            const entry = v.entries[i];

            // Check prev_hash
            if (entry.prev_hash !== prevHash) {
                console.log(`  ❌ Entry ${i}: prev_hash mismatch!`);
                console.log(`     Expected: ${prevHash}`);
                console.log(`     Got:      ${entry.prev_hash}`);
                allHashesMatch = false;
                break;
            }

            // Compute entry_hash
            const computedHash = await computeEntryHash(entry.prev_hash, entry);
            if (entry.entry_hash !== computedHash) {
                console.log(`  ❌ Entry ${i}: entry_hash mismatch!`);
                console.log(`     Expected: ${entry.entry_hash}`);
                console.log(`     Computed: ${computedHash}`);
                allHashesMatch = false;
                break;
            }

            prevHash = entry.entry_hash;
        }

        if (!allHashesMatch) {
            failed++;
            console.log(`  Result: FAIL\n`);
            continue;
        }

        // Check final head
        if (prevHash !== v.head) {
            console.log(`  ❌ Head mismatch!`);
            console.log(`     Expected: ${v.head}`);
            console.log(`     Got:      ${prevHash}`);
            failed++;
            console.log(`  Result: FAIL\n`);
            continue;
        }

        // Test 2: verifyChain function
        const result = await verifyChain(v.entries);
        if (!result.ok || !result.chained || result.entries !== v.entries.length || result.head !== v.head) {
            console.log(`  ❌ verifyChain failed:`);
            console.log(`     Result: ${JSON.stringify(result, null, 2)}`);
            failed++;
            console.log(`  Result: FAIL\n`);
            continue;
        }

        // Test 3: JSONL round-trip
        const jsonl = v.entries.map(e => JSON.stringify(e)).join('\n');
        const jsonlResult = await verifyChainFromJsonl(jsonl);
        if (!jsonlResult.ok || !jsonlResult.chained) {
            console.log(`  ❌ JSONL round-trip failed:`);
            console.log(`     Result: ${JSON.stringify(jsonlResult, null, 2)}`);
            failed++;
            console.log(`  Result: FAIL\n`);
            continue;
        }

        console.log(`  ✅ All tests passed`);
        console.log(`     Head: ${prevHash}`);
        console.log(`  Result: PASS\n`);
        passed++;
    }

    console.log('='.repeat(50));
    console.log(`Results: ${passed} passed, ${failed} failed`);
    console.log('='.repeat(50));

    return failed === 0;
}

// Run tests
let passed = 0;
let failed = 0;
const success = await runTests();
process.exit(success ? 0 : 1);