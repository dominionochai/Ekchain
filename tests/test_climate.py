"""Tests for climate forensics tools (EPA CEMS data)."""

from __future__ import annotations

from pathlib import Path

import pytest

from ekchain.server.app import get_ctx, init_server
from ekchain.server.tool_access import ANALYSTS, EXECUTORS, PLANNERS, get_tools_for_role
from ekchain.server.tools.case import scan_evidence

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "climate"


@pytest.fixture(autouse=True)
def _init_server(tmp_path: Path) -> None:
    """Initialize server context for each test."""
    init_server(db_dir=tmp_path)


@pytest.fixture()
async def _case(tmp_path: Path) -> str:
    """Create a test case with the climate fixtures as evidence."""
    # Use the fixture directory as evidence
    result = await scan_evidence(str(FIXTURE_DIR), case_id="test-climate", replace=True)
    assert result["status"] == "success"
    case_id = result["case_id"]
    # Ensure the case is loaded
    ctx = get_ctx()
    assert ctx.case_id == case_id
    return case_id


class TestClimateToolRegistration:
    """Verify climate tools are registered and available to correct roles."""

    def test_climate_tools_registered(self) -> None:
        """All three climate tools should be registered."""
        tools = get_tools_for_role(EXECUTORS | PLANNERS | ANALYSTS)
        assert "mcp__ekchain__parse_cems_hourly" in tools
        assert "mcp__ekchain__check_emissions_load_consistency" in tools
        assert "mcp__ekchain__derive_operating_timeline" in tools

    def test_climate_tools_available_to_executors(self) -> None:
        """Climate tools should be available to EXECUTORS."""
        exec_tools = get_tools_for_role(EXECUTORS)
        assert "mcp__ekchain__parse_cems_hourly" in exec_tools
        assert "mcp__ekchain__check_emissions_load_consistency" in exec_tools
        assert "mcp__ekchain__derive_operating_timeline" in exec_tools

    def test_climate_tools_not_available_to_planners(self) -> None:
        """Climate tools should NOT be available to PLANNERS."""
        plan_tools = get_tools_for_role(PLANNERS)
        assert "mcp__ekchain__parse_cems_hourly" not in plan_tools
        assert "mcp__ekchain__check_emissions_load_consistency" not in plan_tools
        assert "mcp__ekchain__derive_operating_timeline" not in plan_tools

    def test_climate_tools_not_available_to_analysts(self) -> None:
        """Climate tools should NOT be available to ANALYSTS."""
        analyst_tools = get_tools_for_role(ANALYSTS)
        assert "mcp__ekchain__parse_cems_hourly" not in analyst_tools
        assert "mcp__ekchain__check_emissions_load_consistency" not in analyst_tools
        assert "mcp__ekchain__derive_operating_timeline" not in analyst_tools


class TestParseCemsHourly:
    """Tests for parse_cems_hourly tool."""

    @pytest.mark.asyncio()
    async def test_parse_stable_series(self, _case: str) -> None:
        """Parse a stable series and verify indexing."""
        from ekchain.server.tools.climate import parse_cems_hourly

        result = await parse_cems_hourly(str(FIXTURE_DIR / "cems_hourly_stable.csv"))

        assert result["status"] == "success"
        assert result["total_rows"] == 48
        assert "U1" in result["per_unit"]
        unit = result["per_unit"]["U1"]
        assert unit["row_count"] == 48
        assert unit["missing_hours"] == 0
        assert unit["null_or_negative_values"] == 0
        assert unit["zero_load_hours"] == 0
        assert unit["totals"]["co2Mass_short_tons"] == 4320.0  # 48 * 90
        assert result["windows_indexed"] > 0

    @pytest.mark.asyncio()
    async def test_parse_co2_drop_detects_anomaly(self, _case: str) -> None:
        """Parse a series with 40% co2Mass drop."""
        from ekchain.server.tools.climate import parse_cems_hourly

        result = await parse_cems_hourly(str(FIXTURE_DIR / "cems_hourly_co2_drop_flat_heat.csv"))

        assert result["status"] == "success"
        assert result["total_rows"] == 36
        unit = result["per_unit"]["U1"]
        assert unit["row_count"] == 36
        # 4 hours at 54 instead of 90 = 4 * 54 + 32 * 90 = 216 + 2880 = 3096
        # Actually: hours 0-12 = 13h at 90, hours 13-16 = 4h at 54,
        # hours 17-23 = 7h at 90, hours 24-35 = 12h at 90
        # Total: 32*90 + 4*54 = 2880 + 216 = 3096
        assert unit["totals"]["co2Mass_short_tons"] == 3096.0

    @pytest.mark.asyncio()
    async def test_parse_shutdown_detects_zero_load(self, _case: str) -> None:
        """Parse a series with 12-hour shutdown."""
        from ekchain.server.tools.climate import parse_cems_hourly

        result = await parse_cems_hourly(str(FIXTURE_DIR / "cems_hourly_shutdown_12h.csv"))

        assert result["status"] == "success"
        assert result["total_rows"] == 72
        unit = result["per_unit"]["U1"]
        assert unit["row_count"] == 72
        # 12 hours of zero load (hours 0-11 on 2024-01-03)
        assert unit["zero_load_hours"] == 12
        # 12 hours of zero values (load=0, heat=0, co2=0) -
        # these are valid zero readings, not null/negative
        # null_or_negative_values counts None/missing or negative values, not zeros
        assert result["windows_indexed"] > 0

    @pytest.mark.asyncio()
    async def test_parse_with_gaps_detects_missing_hours(self, _case: str) -> None:
        """Parse a series with gaps and verify missing hour detection."""
        from ekchain.server.tools.climate import parse_cems_hourly

        result = await parse_cems_hourly(str(FIXTURE_DIR / "cems_hourly_with_gaps.csv"))

        assert result["status"] == "success"
        unit = result["per_unit"]["U1"]
        assert unit["missing_hours"] > 0

    @pytest.mark.asyncio()
    async def test_parse_missing_file_returns_error(self, _case: str) -> None:
        """Missing file should return error."""
        from ekchain.server.tools.climate import parse_cems_hourly

        result = await parse_cems_hourly("/nonexistent/path.csv")
        assert result["status"] == "error"
        # Path policy check happens before file existence check
        err = result["error_message"].lower()
        assert "error" in err or "access" in err or "not found" in err

    @pytest.mark.asyncio()
    async def test_parse_reports_co2_flag_counts(self, _case: str) -> None:
        """Parse should report per-unit co2MassMeasureFlg hour counts."""
        from ekchain.server.tools.climate import parse_cems_hourly

        result = await parse_cems_hourly(
            str(FIXTURE_DIR / "cems_hourly_mixed_flags.csv")
        )

        assert result["status"] == "success"
        assert "optional_columns_present" in result
        assert "co2MassMeasureFlg" in result["optional_columns_present"]
        unit = result["per_unit"]["U1"]
        assert unit["co2_measure_flag_counts"]["Measured"] == 36
        assert unit["co2_measure_flag_counts"]["Calculated"] == 12


class TestCheckEmissionsLoadConsistency:
    """Tests for check_emissions_load_consistency tool."""

    @pytest.mark.asyncio()
    async def test_stable_series_no_flags(self) -> None:
        """Stable series should produce no ratio deviation flags."""
        from ekchain.server.tools.climate import check_emissions_load_consistency

        result = await check_emissions_load_consistency(
            str(FIXTURE_DIR / "cems_hourly_stable.csv"), "U1", window_hours=24, tolerance_pct=15
        )

        assert result["status"] == "success"
        assert result["unit_id"] == "U1"
        # All ratios are 90/1000 = 0.09, median = 0.09, deviation = 0
        ratio_flags = [f for f in result["flags"] if f["type"] == "ratio_deviation"]
        assert len(ratio_flags) == 0
        assert result["windows_indexed"] > 0

    @pytest.mark.asyncio()
    async def test_co2_drop_flat_heat_detects_flags(self, _case: str) -> None:
        """co2Mass drop with flat heatInput should flag ratio deviation and emissions drop."""
        from ekchain.server.tools.climate import check_emissions_load_consistency

        # Use smaller window to capture the ratio deviation
        # (4-hour drop in 24h window = median unchanged)
        result = await check_emissions_load_consistency(
            str(FIXTURE_DIR / "cems_hourly_co2_drop_flat_heat.csv"),
            "U1",
            window_hours=6,
            tolerance_pct=15,
        )

        assert result["status"] == "success"
        # Should have ratio deviation flags (window covering the drop)
        ratio_flags = [f for f in result["flags"] if f["type"] == "ratio_deviation"]
        assert len(ratio_flags) > 0
        # Should have emissions drop with flat load flags
        drop_flags = [f for f in result["flags"] if f["type"] == "emissions_drop_flat_load"]
        assert len(drop_flags) > 0
        # Verify the note about measured vs calculated CO2
        assert "measured" in result["note"].lower()
        assert "calculated" in result["note"].lower()

    @pytest.mark.asyncio()
    async def test_missing_file_returns_error(self, _case: str) -> None:
        """Missing file should return error."""
        from ekchain.server.tools.climate import check_emissions_load_consistency

        result = await check_emissions_load_consistency("/nonexistent.csv", "U1")
        assert result["status"] == "error"
        # Path policy check happens before file existence check
        err = result["error_message"].lower()
        assert "error" in err or "access" in err or "not found" in err

    @pytest.mark.asyncio()
    async def test_unknown_unit_returns_error(self, _case: str) -> None:
        """Unknown unit should return error."""
        from ekchain.server.tools.climate import check_emissions_load_consistency

        result = await check_emissions_load_consistency(
            str(FIXTURE_DIR / "cems_hourly_stable.csv"), "UNKNOWN"
        )
        assert result["status"] == "error"
        assert "not found" in result["error_message"]

    @pytest.mark.asyncio()
    async def test_no_measure_flag_returns_inconclusive(self, _case: str) -> None:
        """Missing co2MassMeasureFlg column should return inconclusive."""
        from ekchain.server.tools.climate import check_emissions_load_consistency

        result = await check_emissions_load_consistency(
            str(FIXTURE_DIR / "cems_hourly_no_measure_flag.csv"), "U1"
        )
        assert result["status"] == "inconclusive: no measure flag"
        assert "co2MassMeasureFlg" in result["note"]

    @pytest.mark.asyncio()
    async def test_all_calculated_returns_inconclusive(self, _case: str) -> None:
        """All-Calculated unit should return inconclusive (below 70% Measured)."""
        from ekchain.server.tools.climate import check_emissions_load_consistency

        result = await check_emissions_load_consistency(
            str(FIXTURE_DIR / "cems_hourly_calculated.csv"), "U1"
        )
        assert result["status"] == "inconclusive"
        assert result["measured_fraction"] == 0.0
        assert result["hours_measured"] == 0
        assert result["hours_total"] == 48

    @pytest.mark.asyncio()
    async def test_mixed_flags_above_threshold_detects_flags(self, _case: str) -> None:
        """Mixed flags with >=70% Measured should still detect CO2 drop in Measured hours."""
        from ekchain.server.tools.climate import check_emissions_load_consistency

        result = await check_emissions_load_consistency(
            str(FIXTURE_DIR / "cems_hourly_mixed_flags.csv"),
            "U1",
            window_hours=6,
            tolerance_pct=15,
        )
        assert result["status"] == "success"
        assert result["measured_fraction"] == 0.75
        # Calculated hours are excluded; drop at Jan 1 hours 13-16 should be detected
        ratio_flags = [f for f in result["flags"] if f["type"] == "ratio_deviation"]
        assert len(ratio_flags) > 0


class TestDeriveOperatingTimeline:
    """Tests for derive_operating_timeline tool."""

    @pytest.mark.asyncio()
    async def test_stable_series_all_online(self, _case: str) -> None:
        """Stable series should be all ONLINE."""
        from ekchain.server.tools.climate import derive_operating_timeline

        result = await derive_operating_timeline(
            str(FIXTURE_DIR / "cems_hourly_stable.csv"), "U1"
        )

        assert result["status"] == "success"
        assert result["unit_id"] == "U1"
        # All intervals should be ONLINE
        for interval in result["intervals"]:
            assert interval["state"] == "ONLINE"
        assert result["windows_indexed"] > 0

    @pytest.mark.asyncio()
    async def test_shutdown_detects_offline_interval(self, _case: str) -> None:
        """12-hour shutdown should produce OFFLINE interval."""
        from ekchain.server.tools.climate import derive_operating_timeline

        result = await derive_operating_timeline(
            str(FIXTURE_DIR / "cems_hourly_shutdown_12h.csv"), "U1"
        )

        assert result["status"] == "success"
        # Should have ONLINE, OFFLINE (12h), ONLINE
        offline_intervals = [iv for iv in result["intervals"] if iv["state"] == "OFFLINE"]
        assert len(offline_intervals) == 1
        assert offline_intervals[0]["hours"] == 12
        # Should report zero offline emissions (all zeros in fixture)
        assert offline_intervals[0]["offline_co2_short_tons"] == 0.0
        assert result["windows_indexed"] > 0

    @pytest.mark.asyncio()
    async def test_note_honesty_about_source(self, _case: str) -> None:
        """Note should state derived from CEMS, not SCADA."""
        from ekchain.server.tools.climate import derive_operating_timeline

        result = await derive_operating_timeline(
            str(FIXTURE_DIR / "cems_hourly_stable.csv"), "U1"
        )

        assert result["status"] == "success"
        note = result["note"].lower()
        assert "cems" in note
        assert "scada" in note
        assert "not from scada" in note or "not from scada logs" in note

    @pytest.mark.asyncio()
    async def test_missing_file_returns_error(self, _case: str) -> None:
        """Missing file should return error."""
        from ekchain.server.tools.climate import derive_operating_timeline

        result = await derive_operating_timeline("/nonexistent.csv", "U1")
        assert result["status"] == "error"
        # Path policy check happens before file existence check
        err = result["error_message"].lower()
        assert "error" in err or "access" in err or "not found" in err

    @pytest.mark.asyncio()
    async def test_unknown_unit_returns_error(self, _case: str) -> None:
        """Unknown unit should return error."""
        from ekchain.server.tools.climate import derive_operating_timeline

        result = await derive_operating_timeline(
            str(FIXTURE_DIR / "cems_hourly_stable.csv"), "UNKNOWN"
        )
        assert result["status"] == "error"
        assert "not found" in result["error_message"]

    @pytest.mark.asyncio()
    async def test_optime_zero_forces_offline(self, _case: str) -> None:
        """opTime=0 should force OFFLINE even when load/heat are non-zero."""
        from ekchain.server.tools.climate import derive_operating_timeline

        result = await derive_operating_timeline(
            str(FIXTURE_DIR / "cems_hourly_optime_zero.csv"), "U1"
        )

        assert result["status"] == "success"
        assert result["opTime_used"] is True
        # Hours 0-11 have optime=0 with load=100, heat=1000
        # Without opTime they would be ONLINE, but opTime=0 forces OFFLINE
        offline_intervals = [
            iv for iv in result["intervals"] if iv["state"] == "OFFLINE"
        ]
        assert len(offline_intervals) == 1
        assert offline_intervals[0]["hours"] == 12


class TestCemsClassifier:
    """Tests for CEMS classifier."""

    def test_cems_hourly_classified(self) -> None:
        """cems_hourly*.csv files should be classified as cems_hourly."""
        from ekchain.extractors.classifier import ClassifierConfig, EvidenceClassifier

        classifier = EvidenceClassifier(ClassifierConfig())
        results = classifier.classify(FIXTURE_DIR)

        cems_files = [r for r in results if r.artifact_type == "cems_hourly"]
        assert len(cems_files) == 8
        names = {r.path.name for r in cems_files}
        assert "cems_hourly_stable.csv" in names
        assert "cems_hourly_co2_drop_flat_heat.csv" in names
        assert "cems_hourly_shutdown_12h.csv" in names
        assert "cems_hourly_with_gaps.csv" in names
        assert "cems_hourly_calculated.csv" in names
        assert "cems_hourly_mixed_flags.csv" in names
        assert "cems_hourly_optime_zero.csv" in names
        assert "cems_hourly_no_measure_flag.csv" in names

    def test_other_csv_still_skipped(self) -> None:
        """Other .csv files should still be skipped (not classified as cems_hourly)."""
        from ekchain.extractors.classifier import ClassifierConfig, EvidenceClassifier

        classifier = EvidenceClassifier(ClassifierConfig())
        results = classifier.classify(FIXTURE_DIR)

        # README.md should not be classified
        readme = [r for r in results if r.path.name == "README.md"]
        assert len(readme) == 0
