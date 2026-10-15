"""Tests for ekchain.audit -- AuditLog persistence, provenance, summary, hash chain."""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ekchain.audit import AuditLog, verify_chain
from ekchain.models import SourceRow


class TestToolCallPersistence:
    def test_log_and_has_tool_call(self, tmp_audit_log: AuditLog) -> None:
        tmp_audit_log.log_tool_call(
            tool_call_id="tc_001",
            tool_name="search",
            params={"query": "spinlock"},
            output_hash="sha256:abc",
            duration_ms=150.0,
        )
        assert tmp_audit_log.has_tool_call("tc_001")
        assert not tmp_audit_log.has_tool_call("tc_nonexistent")

    def test_tool_call_ids_returns_copy(self, tmp_audit_log: AuditLog) -> None:
        tmp_audit_log.log_tool_call("tc_a", "tool", {}, "sha256:x")
        ids = tmp_audit_log.tool_call_ids
        ids.add("tc_fake")
        assert "tc_fake" not in tmp_audit_log.tool_call_ids


class TestLoadExisting:
    def test_reload_from_disk(self, tmp_path: Path) -> None:
        log_path = tmp_path / "audit.jsonl"
        log1 = AuditLog(log_path)
        log1.log_tool_call("tc_persist", "search", {}, "sha256:x")

        log2 = AuditLog(log_path)
        assert log2.has_tool_call("tc_persist")

    def test_corrupt_lines_skipped(self, tmp_path: Path) -> None:
        log_path = tmp_path / "audit.jsonl"
        log_path.write_text(
            '{"type":"tool_call","tool_call_id":"tc_ok","tool_name":"x","params":{}}\n'
            "not valid json\n"
            "\n"
            '{"type":"tool_call","tool_call_id":"tc_ok2","tool_name":"y","params":{}}\n'
        )
        log = AuditLog(log_path)
        assert log.has_tool_call("tc_ok")
        assert log.has_tool_call("tc_ok2")

    def test_corrupt_lines_logged_as_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Corrupt JSONL lines should be counted and logged as a warning."""
        log_path = tmp_path / "audit.jsonl"
        log_path.write_text(
            '{"type":"tool_call","tool_call_id":"tc_good","tool_name":"x","params":{}}\n'
            "this is not json\n"
            "also broken {{{{\n"
            '{"type":"tool_call","tool_call_id":"tc_good2","tool_name":"y","params":{}}\n'
        )
        with caplog.at_level(logging.WARNING):
            log = AuditLog(log_path)

        assert log.has_tool_call("tc_good")
        assert log.has_tool_call("tc_good2")
        assert any("2 lines failed to parse" in msg for msg in caplog.messages)


class TestProvenance:
    def test_get_provenance_chain(self, tmp_path: Path) -> None:
        log_path = tmp_path / "audit.jsonl"
        audit = AuditLog(log_path)
        audit.log_tool_call(
            "tc_abc", "search", {"source": "volatility.pslist"}, "sha256:out", duration_ms=100
        )
        audit.log_finding_submission("f-1", ["tc_abc"])

        mock_db = MagicMock()
        mock_db.get_sources.return_value = [
            SourceRow(
                source_id=1,
                case_id="c",
                source_name="volatility.pslist",
                source_path="/ev/mem.mem",
                source_hash="hash",
                extractor="volatility",
                line_count=10,
            )
        ]
        chain = audit.get_provenance_chain("f-1", mock_db)
        assert chain.finding_id == "f-1"
        assert len(chain.tool_calls) == 1
        assert chain.tool_calls[0].tool_call_id == "tc_abc"
        assert len(chain.sources) == 1
        assert chain.sources[0].source_name == "volatility.pslist"

    def test_unknown_finding_raises(self, tmp_audit_log: AuditLog) -> None:
        mock_db = MagicMock()
        with pytest.raises(KeyError, match="No finding with id"):
            tmp_audit_log.get_provenance_chain("nonexistent", mock_db)


class TestExtractSourceNames:
    def test_source_key(self) -> None:
        names = AuditLog._extract_source_names({"source": "vol.pslist"})
        assert names == {"vol.pslist"}

    def test_source_name_key(self) -> None:
        names = AuditLog._extract_source_names({"source_name": "tsk.fls"})
        assert names == {"tsk.fls"}

    def test_channel_key(self) -> None:
        names = AuditLog._extract_source_names({"channel": "Security"})
        assert names == {"evtx.Security"}

    def test_sources_list(self) -> None:
        names = AuditLog._extract_source_names({"sources": ["a", "b", 42]})
        assert names == {"a", "b"}

    def test_empty_params(self) -> None:
        assert AuditLog._extract_source_names({}) == set()


class TestSummary:
    def test_empty_log(self, tmp_path: Path) -> None:
        audit = AuditLog(tmp_path / "empty.jsonl")
        s = audit.summary()
        assert s.total_tool_calls == 0
        assert s.total_findings == 0
        assert s.first_timestamp == ""

    def test_aggregates_counts_and_durations(self, tmp_path: Path) -> None:
        log_path = tmp_path / "audit.jsonl"
        audit = AuditLog(log_path)
        audit.log_tool_call("tc_1", "search", {"q": "x"}, "sha256:a", duration_ms=100)
        audit.log_tool_call("tc_2", "search", {"q": "y"}, "sha256:b", duration_ms=200)
        audit.log_tool_call("tc_3", "correlate", {}, "sha256:c", duration_ms=50)
        audit.log_finding_submission("f-1", ["tc_1"])

        s = audit.summary()
        assert s.total_tool_calls == 3
        assert s.total_findings == 1
        assert s.tool_call_counts["search"] == 2
        assert s.tool_call_counts["correlate"] == 1
        assert s.total_duration_ms == 350.0
        assert s.first_timestamp != ""
        assert s.estimated_cost_usd >= 0

    def test_skips_run_parallel(self, tmp_path: Path) -> None:
        log_path = tmp_path / "audit.jsonl"
        entry = json.dumps(
            {
                "type": "tool_call",
                "tool_call_id": "tc_rp",
                "tool_name": "run_parallel",
                "params": {},
                "output_hash": "sha256:x",
                "duration_ms": 500,
                "timestamp": "2025-01-01T00:00:00Z",
            }
        )
        log_path.write_text(entry + "\n")
        audit = AuditLog(log_path)
        s = audit.summary()
        assert s.total_tool_calls == 0


class TestIngestion:
    def test_log_ingestion_step(self, tmp_audit_log: AuditLog) -> None:
        tmp_audit_log.log_ingestion_step(
            source_name="vol.pslist",
            source_path="/ev/mem.mem",
            source_hash="abc",
            extractor="volatility",
            window_count=10,
            duration_ms=500,
        )
        s = tmp_audit_log.summary()
        assert s.first_timestamp != ""


class TestHashChain:
    """Tests for the hash-chained audit log."""

    def test_new_log_is_chained(self, tmp_path: Path) -> None:
        """A newly created log should be chained with genesis prev_hash."""
        log_path = tmp_path / "audit.jsonl"
        audit = AuditLog(log_path)
        audit.log_tool_call("tc_1", "search", {"q": "x"}, "sha256:a", duration_ms=100)

        # Verify the log is chained
        result = verify_chain(log_path)
        assert result["ok"] is True
        assert result["chained"] is True
        assert result["entries"] == 1
        assert result["first_bad_index"] is None
        assert result["head"] != "0" * 64

    def test_genesis_prev_hash_is_64_zeros(self, tmp_path: Path) -> None:
        """The first entry's prev_hash should be 64 zeros."""
        log_path = tmp_path / "audit.jsonl"
        audit = AuditLog(log_path)
        audit.log_tool_call("tc_1", "search", {"q": "x"}, "sha256:a")

        # Read the first entry directly
        with open(log_path) as f:
            entry = json.loads(f.readline())

        assert entry["prev_hash"] == "0" * 64
        assert "entry_hash" in entry

    def test_second_entry_prev_hash_matches_first_head(self, tmp_path: Path) -> None:
        """The second entry's prev_hash should match the first entry's entry_hash."""
        log_path = tmp_path / "audit.jsonl"
        audit = AuditLog(log_path)
        audit.log_tool_call("tc_1", "search", {"q": "x"}, "sha256:a")
        audit.log_tool_call("tc_2", "search", {"q": "y"}, "sha256:b")

        with open(log_path) as f:
            lines = f.readlines()
            entry1 = json.loads(lines[0])
            entry2 = json.loads(lines[1])

        assert entry2["prev_hash"] == entry1["entry_hash"]

    def test_verify_chain_passes_on_valid_log(self, tmp_path: Path) -> None:
        """verify_chain should return ok=True for a valid log."""
        log_path = tmp_path / "audit.jsonl"
        audit = AuditLog(log_path)
        audit.log_tool_call("tc_1", "search", {"q": "x"}, "sha256:a")
        audit.log_tool_call("tc_2", "search", {"q": "y"}, "sha256:b")
        audit.log_finding_submission("f-1", ["tc_1"])

        result = verify_chain(log_path)
        assert result["ok"] is True
        assert result["chained"] is True
        assert result["entries"] == 3
        assert result["first_bad_index"] is None
        assert result["head"] != ""

    def test_tampered_field_detected(self, tmp_path: Path) -> None:
        """Editing a field in an entry should be detected."""
        log_path = tmp_path / "audit.jsonl"
        audit = AuditLog(log_path)
        audit.log_tool_call("tc_1", "search", {"q": "x"}, "sha256:a")
        audit.log_tool_call("tc_2", "search", {"q": "y"}, "sha256:b")

        # Tamper with the first entry's params
        with open(log_path) as f:
            lines = f.readlines()
        entry1 = json.loads(lines[0])
        entry1["params"]["q"] = "tampered"
        lines[0] = json.dumps(entry1, separators=(",", ":")) + "\n"
        log_path.write_text("".join(lines))

        result = verify_chain(log_path)
        assert result["ok"] is False
        assert result["chained"] is False
        assert result["first_bad_index"] == 1
        assert "entry_hash mismatch" in result["reason"]

    def test_deleted_line_detected(self, tmp_path: Path) -> None:
        """Deleting a line should be detected."""
        log_path = tmp_path / "audit.jsonl"
        audit = AuditLog(log_path)
        audit.log_tool_call("tc_1", "search", {"q": "x"}, "sha256:a")
        audit.log_tool_call("tc_2", "search", {"q": "y"}, "sha256:b")
        audit.log_tool_call("tc_3", "search", {"q": "z"}, "sha256:c")

        # Delete the middle line
        with open(log_path) as f:
            lines = f.readlines()
        # Remove line 2 (index 1)
        lines = [lines[0], lines[2]]
        log_path.write_text("".join(lines))

        result = verify_chain(log_path)
        assert result["ok"] is False
        assert result["chained"] is False
        assert result["first_bad_index"] == 2
        assert "prev_hash mismatch" in result["reason"]

    def test_reordered_lines_detected(self, tmp_path: Path) -> None:
        """Reordering lines should be detected."""
        log_path = tmp_path / "audit.jsonl"
        audit = AuditLog(log_path)
        audit.log_tool_call("tc_1", "search", {"q": "x"}, "sha256:a")
        audit.log_tool_call("tc_2", "search", {"q": "y"}, "sha256:b")
        audit.log_tool_call("tc_3", "search", {"q": "z"}, "sha256:c")

        # Swap lines 2 and 3
        with open(log_path) as f:
            lines = f.readlines()
        lines = [lines[0], lines[2], lines[1]]
        log_path.write_text("".join(lines))

        result = verify_chain(log_path)
        assert result["ok"] is False
        assert result["chained"] is False
        assert result["first_bad_index"] == 2
        assert "prev_hash mismatch" in result["reason"]

    def test_inserted_line_detected(self, tmp_path: Path) -> None:
        """Inserting a fake line should be detected."""
        log_path = tmp_path / "audit.jsonl"
        audit = AuditLog(log_path)
        audit.log_tool_call("tc_1", "search", {"q": "x"}, "sha256:a")
        audit.log_tool_call("tc_2", "search", {"q": "y"}, "sha256:b")

        # Insert a fake line between them
        with open(log_path) as f:
            lines = f.readlines()
        fake_entry = {
            "type": "tool_call",
            "tool_call_id": "tc_fake",
            "tool_name": "fake",
            "params": {},
            "output_hash": "sha256:fake",
            "duration_ms": 0,
            "timestamp": "2025-01-01T00:00:00Z",
            "prev_hash": lines[0].strip(),
            "entry_hash": "fake_hash",
        }
        lines = [lines[0], json.dumps(fake_entry) + "\n", lines[1]]
        log_path.write_text("".join(lines))

        result = verify_chain(log_path)
        assert result["ok"] is False
        assert result["chained"] is False
        assert result["first_bad_index"] == 2
        # The inserted entry has wrong hash, so entry_hash mismatch

    def test_concurrent_appends_still_verify(self, tmp_path: Path) -> None:
        """Concurrent appends from multiple threads should still verify."""
        log_path = tmp_path / "audit.jsonl"
        audit = AuditLog(log_path)

        def log_many(start: int, count: int):
            for i in range(count):
                audit.log_tool_call(
                    f"tc_{start + i}", "search", {"q": str(start + i)}, f"sha256:{start + i}"
                )

        threads = []
        for t in range(4):
            thread = threading.Thread(target=log_many, args=(t * 10, 10))
            threads.append(thread)
            thread.start()

        for thread in threads:
            thread.join()

        result = verify_chain(log_path)
        assert result["ok"] is True
        assert result["chained"] is True
        assert result["entries"] == 40

    def test_legacy_log_loads_as_unchained(self, tmp_path: Path) -> None:
        """A legacy log without hash fields should load but be marked unchained."""
        log_path = tmp_path / "legacy.jsonl"
        # Write legacy entries without hash fields
        log_path.write_text(
            '{"type":"tool_call","tool_call_id":"tc_1","tool_name":"search","params":{"q":"x"}}\n'
            '{"type":"tool_call","tool_call_id":"tc_2","tool_name":"search","params":{"q":"y"}}\n'
        )

        audit = AuditLog(log_path)
        assert audit.has_tool_call("tc_1")
        assert audit.has_tool_call("tc_2")

        result = verify_chain(log_path)
        assert result["ok"] is False
        assert result["chained"] is False

    def test_verify_chain_empty_log(self, tmp_path: Path) -> None:
        """verify_chain on empty log should return ok with genesis head."""
        log_path = tmp_path / "empty.jsonl"
        log_path.write_text("")
        result = verify_chain(log_path)
        assert result["ok"] is True
        assert result["entries"] == 0
        assert result["head"] == "0" * 64
        assert result["chained"] is True


class TestVerifyChainFunction:
    """Tests for the verify_chain function."""

    def test_verify_chain_on_nonexistent_file(self, tmp_path: Path) -> None:
        """verify_chain on nonexistent file should return ok with genesis head."""
        log_path = tmp_path / "nonexistent.jsonl"
        result = verify_chain(log_path)
        assert result["ok"] is True
        assert result["entries"] == 0
        assert result["head"] == "0" * 64

    def test_verify_chain_returns_correct_structure(self, tmp_path: Path) -> None:
        """verify_chain should return all expected keys."""
        log_path = tmp_path / "audit.jsonl"
        audit = AuditLog(log_path)
        audit.log_tool_call("tc_1", "search", {"q": "x"}, "sha256:a")

        result = verify_chain(log_path)
        expected_keys = {"ok", "entries", "head", "first_bad_index", "reason", "chained"}
        assert set(result.keys()) == expected_keys
